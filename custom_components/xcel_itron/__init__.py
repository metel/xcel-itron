"""The Xcel Itron smart meter integration."""

from __future__ import annotations

import logging
import ssl
from datetime import timedelta

import yaml
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .certificates import compute_lfdi_from_certificate, resolve_path
from .const import (
    CONF_CERT_PATH,
    CONF_HOST,
    CONF_KEY_PATH,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import XcelItronCoordinator
from .meter import (
    MeterAuthError,
    MeterError,
    MeterProtocolError,
    XcelMeterClient,
    load_endpoints,
    normalize_endpoints,
    select_config_version,
    supported_config_versions,
)
from .models import XcelItronRuntimeData
from .repairs import (
    async_create_credentials_issue,
    async_delete_credentials_issue,
)
from .sensor import build_sensor_specs

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

XcelItronConfigEntry = ConfigEntry[XcelItronRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: XcelItronConfigEntry) -> bool:
    """Set up the Xcel Itron integration from a config entry."""
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]
    cert_path = resolve_path(hass.config.path(), entry.data[CONF_CERT_PATH])
    key_path = resolve_path(hass.config.path(), entry.data[CONF_KEY_PATH])

    if not cert_path.is_file() or not key_path.is_file():
        raise ConfigEntryNotReady(
            "The configured certificate or private key file does not exist"
        )

    try:
        client_lfdi = await hass.async_add_executor_job(
            compute_lfdi_from_certificate, cert_path
        )
    except (OSError, ValueError, TypeError) as err:
        raise ConfigEntryNotReady(
            "Unable to read the configured client certificate"
        ) from err

    try:
        client = XcelMeterClient(host, port, cert_path, key_path)
    except (OSError, ValueError, ssl.SSLError) as err:
        raise ConfigEntryNotReady(
            f"Unable to load the client certificate: {err}"
        ) from err

    try:
        hardware = await client.async_get_hardware_info()
    except MeterAuthError as err:
        async_create_credentials_issue(hass, entry.entry_id, host, client_lfdi)
        await client.async_close()
        raise ConfigEntryNotReady(
            "The meter rejected the client certificate. Verify that the LFDI "
            "is registered on the Xcel Launchpad"
        ) from err
    except MeterError as err:
        await client.async_close()
        raise ConfigEntryNotReady(f"Unable to connect to the meter: {err}") from err

    try:
        supported_versions = await hass.async_add_executor_job(
            supported_config_versions
        )
        config_version = select_config_version(supported_versions, hardware.sw_version)
        endpoint_definitions = await hass.async_add_executor_job(
            load_endpoints, config_version
        )
        endpoints = normalize_endpoints(endpoint_definitions)
    except (
        MeterProtocolError,
        OSError,
        yaml.YAMLError,
        KeyError,
        TypeError,
    ) as err:
        await client.async_close()
        raise ConfigEntryNotReady(
            f"Unable to load endpoint configuration: {err}"
        ) from err

    specs = build_sensor_specs(endpoints)
    scan_interval: int = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    coordinator = XcelItronCoordinator(
        hass,
        client,
        endpoints,
        specs,
        timedelta(seconds=scan_interval),
        entry.entry_id,
        client_lfdi,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await client.async_close()
        raise

    entry.runtime_data = XcelItronRuntimeData(
        client=client,
        coordinator=coordinator,
        hardware=hardware,
        specs=specs,
        endpoints=endpoints,
        client_lfdi=client_lfdi,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: XcelItronConfigEntry) -> bool:
    """Unload an Xcel Itron config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            await runtime.client.async_close()
        async_delete_credentials_issue(hass, entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: XcelItronConfigEntry) -> None:
    """Clean up when a config entry is removed."""
    async_delete_credentials_issue(hass, entry.entry_id)


async def _async_update_listener(
    hass: HomeAssistant, entry: XcelItronConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
