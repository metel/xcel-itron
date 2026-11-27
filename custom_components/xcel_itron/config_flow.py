"""Config flow for the Xcel Itron integration."""

from __future__ import annotations

import logging
import ssl
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # Home Assistant < 2025.2
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from .certificates import (
    compute_lfdi_from_certificate,
    generate_client_certificate,
    resolve_path,
    write_credentials,
)
from .const import (
    CONF_CERT_PATH,
    CONF_CREDENTIAL_MODE,
    CONF_HOST,
    CONF_KEY_PATH,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CREDENTIAL_MODE_EXISTING,
    CREDENTIAL_MODE_GENERATE,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LAUNCHPAD_URL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .meter import (
    MeterAuthError,
    MeterConnectionError,
    MeterError,
    XcelMeterClient,
)

_LOGGER = logging.getLogger(__name__)

_CREDENTIAL_CHOICES = {
    CREDENTIAL_MODE_GENERATE: "Generate a new certificate",
    CREDENTIAL_MODE_EXISTING: "Use an existing certificate",
}


class XcelItronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Xcel Itron config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._name: str = DEFAULT_NAME
        self._cert_pem: bytes | None = None
        self._key_pem: bytes | None = None
        self._lfdi: str | None = None

    def _user_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host or ""): vol.All(
                    str, vol.Length(min=1)
                ),
                vol.Required(CONF_PORT, default=self._port): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_NAME, default=self._name): vol.All(
                    str, vol.Length(min=1)
                ),
                vol.Required(
                    CONF_CREDENTIAL_MODE,
                    default=CREDENTIAL_MODE_GENERATE,
                ): vol.In(_CREDENTIAL_CHOICES),
            }
        )

    def _existing_schema(self, user_input: dict[str, Any] | None = None) -> vol.Schema:
        cert_default = "xcel_itron/cert.pem"
        key_default = "xcel_itron/key.pem"
        if user_input is not None:
            cert_default = user_input.get(CONF_CERT_PATH, cert_default)
            key_default = user_input.get(CONF_KEY_PATH, key_default)

        return vol.Schema(
            {
                vol.Required(CONF_CERT_PATH, default=cert_default): vol.All(
                    str, vol.Length(min=1)
                ),
                vol.Required(CONF_KEY_PATH, default=key_default): vol.All(
                    str, vol.Length(min=1)
                ),
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = user_input[CONF_PORT]
            self._name = user_input[CONF_NAME].strip() or DEFAULT_NAME

            if user_input[CONF_CREDENTIAL_MODE] == CREDENTIAL_MODE_GENERATE:
                return await self.async_step_lfdi()

            return await self.async_step_existing()

        return self.async_show_form(
            step_id="user",
            data_schema=self._user_schema(),
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a meter discovered over mDNS."""
        host = getattr(discovery_info, "ip_address", None)
        if not host:
            addresses = getattr(discovery_info, "ip_addresses", None) or []
            host = addresses[0] if addresses else None
        if not host:
            host = getattr(discovery_info, "hostname", None) or getattr(
                discovery_info, "host", None
            )

        if not host:
            return self.async_abort(reason="cannot_connect")

        port = discovery_info.port or DEFAULT_PORT
        self._async_abort_entries_match({CONF_HOST: str(host), CONF_PORT: port})

        self._host = str(host)
        self._port = port
        discovered_name = (discovery_info.name or "").split(".")[0].strip()
        if discovered_name:
            self._name = discovered_name
        self.context["title_placeholders"] = {"name": self._name}

        return await self.async_step_user()

    async def async_step_lfdi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Generate credentials and ask the user to register the LFDI."""
        if self._lfdi is None:
            try:
                (
                    self._cert_pem,
                    self._key_pem,
                    self._lfdi,
                ) = await self.hass.async_add_executor_job(generate_client_certificate)
            except Exception:
                _LOGGER.exception("Failed to generate client certificate")
                return self.async_abort(reason="certificate_generation_failed")

            await self.async_set_unique_id(self._lfdi)
            self._abort_if_unique_id_configured()

        if user_input is not None:
            assert self._cert_pem is not None
            assert self._key_pem is not None
            assert self._lfdi is not None

            try:
                cert_path, key_path = await self.hass.async_add_executor_job(
                    write_credentials,
                    self.hass.config.path(),
                    self._lfdi,
                    self._cert_pem,
                    self._key_pem,
                )
            except OSError:
                _LOGGER.exception("Failed to write client credentials")
                return self.async_abort(reason="certificate_generation_failed")

            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_NAME: self._name,
                    CONF_CERT_PATH: cert_path,
                    CONF_KEY_PATH: key_path,
                },
            )

        return self.async_show_form(
            step_id="lfdi",
            data_schema=vol.Schema({}),
            description_placeholders={
                "lfdi": self._lfdi,
                "launchpad_url": LAUNCHPAD_URL,
            },
        )

    async def async_step_existing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use an existing certificate and key pair."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cert_path = resolve_path(
                self.hass.config.path(), user_input[CONF_CERT_PATH].strip()
            )
            key_path = resolve_path(
                self.hass.config.path(), user_input[CONF_KEY_PATH].strip()
            )

            if not cert_path.is_file() or not key_path.is_file():
                errors["base"] = "invalid_certificate"
            else:
                try:
                    lfdi = await self.hass.async_add_executor_job(
                        compute_lfdi_from_certificate, cert_path
                    )
                except (OSError, ValueError, TypeError):
                    errors["base"] = "invalid_certificate"
                else:
                    await self.async_set_unique_id(lfdi)
                    self._abort_if_unique_id_configured()

                    try:
                        client = XcelMeterClient(
                            self._host or "",
                            self._port,
                            cert_path,
                            key_path,
                        )
                    except (OSError, ValueError, ssl.SSLError):
                        errors["base"] = "invalid_certificate"
                    else:
                        try:
                            await client.async_get_hardware_info()
                        except MeterAuthError:
                            errors["base"] = "invalid_auth"
                        except MeterConnectionError:
                            errors["base"] = "cannot_connect"
                        except MeterError:
                            errors["base"] = "unknown"
                        finally:
                            await client.async_close()

                    if not errors:
                        return self.async_create_entry(
                            title=self._name,
                            data={
                                CONF_HOST: self._host,
                                CONF_PORT: self._port,
                                CONF_NAME: self._name,
                                CONF_CERT_PATH: user_input[CONF_CERT_PATH].strip(),
                                CONF_KEY_PATH: user_input[CONF_KEY_PATH].strip(),
                            },
                        )

        return self.async_show_form(
            step_id="existing",
            data_schema=self._existing_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure the meter host and port."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            cert_path = resolve_path(
                self.hass.config.path(), entry.data[CONF_CERT_PATH]
            )
            key_path = resolve_path(self.hass.config.path(), entry.data[CONF_KEY_PATH])

            try:
                client = XcelMeterClient(host, port, cert_path, key_path)
            except (OSError, ValueError, ssl.SSLError):
                errors["base"] = "invalid_certificate"
            else:
                try:
                    await client.async_get_hardware_info()
                except MeterAuthError:
                    errors["base"] = "invalid_auth"
                except MeterConnectionError:
                    errors["base"] = "cannot_connect"
                except MeterError:
                    errors["base"] = "unknown"
                finally:
                    await client.async_close()

            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_PORT: port},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST,
                        default=user_input.get(CONF_HOST, entry.data[CONF_HOST])
                        if user_input
                        else entry.data[CONF_HOST],
                    ): vol.All(str, vol.Length(min=1)),
                    vol.Required(
                        CONF_PORT,
                        default=user_input.get(CONF_PORT, entry.data[CONF_PORT])
                        if user_input
                        else entry.data[CONF_PORT],
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> XcelItronOptionsFlow:
        """Return the options flow."""
        return XcelItronOptionsFlow()


class XcelItronOptionsFlow(OptionsFlow):
    """Options flow for the Xcel Itron integration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )
