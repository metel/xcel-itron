"""Meter communication and endpoint parsing for the Xcel Itron integration."""

from __future__ import annotations

import asyncio
import logging
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from packaging.version import InvalidVersion, Version

from .const import (
    ENDPOINTS_DIR,
    IEEE_PREFIX,
    MAX_CONCURRENT_REQUESTS,
    REQUEST_ATTEMPTS,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF,
)

_LOGGER = logging.getLogger(__name__)

# Our target cipher is: ECDHE-ECDSA-AES128-CCM8
# Security level 0 is required to allow CCM8 ciphers in OpenSSL 3.x.
CIPHERS = "ECDHE-ECDSA-AES128-CCM8:@SECLEVEL=0"

_TLS_ERROR_MARKERS = (
    "certificate",
    "cert_",
    "sslv3 alert",
    "sslv3_alert",
    "tlsv1 alert",
    "tlsv1_alert",
    "tlsv13 alert",
    "tlsv13_alert",
    "handshake failure",
    "handshake_failure",
    "unknown ca",
    "bad cert",
    "wrong version number",
    "alert",
)


class MeterError(Exception):
    """Base error for meter communication."""


class MeterConnectionError(MeterError):
    """Raised when the meter cannot be reached."""


class MeterAuthError(MeterError):
    """Raised when the meter rejects the TLS client certificate."""


class MeterProtocolError(MeterError):
    """Raised when the meter returns an unexpected response."""


@dataclass(slots=True, frozen=True)
class HardwareInfo:
    """Hardware information reported by the meter."""

    lfdi: str
    manufacturer: str
    sw_version: str


@dataclass(slots=True, frozen=True)
class EndpointConfig:
    """A normalized meter endpoint definition."""

    name: str
    url: str
    tags: dict[str, Any]


def normalize_endpoints(
    endpoints: list[dict[str, Any]],
) -> list[EndpointConfig]:
    """Flatten the endpoint YAML structure into EndpointConfig objects."""
    normalized: list[EndpointConfig] = []

    for endpoint in endpoints:
        for name, config in endpoint.items():
            normalized.append(
                EndpointConfig(
                    name=name,
                    url=config["url"],
                    tags=config.get("tags", {}),
                )
            )

    return normalized


def create_ssl_context(cert_path: str | Path, key_path: str | Path) -> ssl.SSLContext:
    """Create the SSL context required by the Itron meter."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(CIPHERS)
    context.load_cert_chain(str(cert_path), str(key_path))

    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        context.options |= ssl.OP_LEGACY_SERVER_CONNECT
    context.options |= ssl.OP_NO_COMPRESSION

    return context


def _is_tls_error(error: BaseException) -> bool:
    """Return True when an exception chain points at a TLS/certificate error."""
    seen: set[int] = set()
    current: BaseException | None = error

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (
                ssl.SSLError,
                aiohttp.ClientConnectorCertificateError,
                aiohttp.ClientConnectorSSLError,
            ),
        ):
            return True

        message = str(current).lower()
        if any(marker in message for marker in _TLS_ERROR_MARKERS):
            return True

        current = current.__cause__ or current.__context__

    return False


def endpoint_data_key(url: str, tag: str) -> str:
    """Return the unique key used for a reading in coordinator data."""
    return f"{url}#{tag}"


def _add_reading(
    root: ET.Element,
    key: str,
    xml_tag: str,
    readings: dict[str, str],
) -> None:
    element = root.find(f".//{IEEE_PREFIX}{xml_tag}")
    if element is not None and element.text is not None:
        readings[key] = element.text.strip()


def parse_endpoint_response(response: str, endpoint: EndpointConfig) -> dict[str, str]:
    """Extract the configured readings from an endpoint XML response."""
    root = ET.fromstring(response)
    readings: dict[str, str] = {}

    for tag_name, tag_definition in endpoint.tags.items():
        if isinstance(tag_definition, list):
            for item in tag_definition:
                for sub_name in item:
                    _add_reading(root, f"{tag_name}_{sub_name}", sub_name, readings)
        else:
            _add_reading(root, tag_name, tag_name, readings)

    return readings


def supported_config_versions(
    endpoints_dir: Path = ENDPOINTS_DIR,
) -> list[Version]:
    """Return the endpoint configuration versions bundled with the integration."""
    versions: list[Version] = []
    for file_path in endpoints_dir.glob("endpoints_*.yaml"):
        version_string = file_path.stem.removeprefix("endpoints_").replace("_", ".")
        try:
            versions.append(Version(version_string))
        except InvalidVersion:
            _LOGGER.warning("Ignoring invalid endpoint config file %s", file_path)

    return sorted(versions)


def select_config_version(
    supported_versions: list[Version], meter_sw_version: str
) -> Version:
    """Select the endpoint config that best matches the meter software version."""
    if not supported_versions:
        raise MeterProtocolError("No endpoint configuration files found")

    supported_versions = sorted(supported_versions)

    try:
        meter_version = Version(meter_sw_version)
    except InvalidVersion:
        _LOGGER.warning(
            "Could not parse meter software version %s; using newest config %s",
            meter_sw_version,
            supported_versions[-1],
        )
        return supported_versions[-1]

    selected_version: Version | None = None

    # Prefer the highest config that is <= the meter version with a matching
    # major.minor pair.
    for version in supported_versions:
        if version > meter_version:
            continue
        if version.major != meter_version.major:
            continue
        if version.minor != meter_version.minor:
            continue
        selected_version = version

    # No major.minor match - fall back to the highest config <= meter version.
    if selected_version is None:
        for version in supported_versions:
            if version <= meter_version:
                selected_version = version

    # Meter is older than every config - use the lowest available.
    if selected_version is None:
        selected_version = supported_versions[0]
        _LOGGER.error(
            "Meter version %s is older than any supported config", meter_version
        )
        _LOGGER.error("Defaulting to lowest available config: %s", selected_version)

    return selected_version


def load_endpoints(
    config_version: Version, endpoints_dir: Path = ENDPOINTS_DIR
) -> list[dict[str, Any]]:
    """Load the endpoint definitions for a config version."""
    file_path = endpoints_dir / (
        f"endpoints_{str(config_version).replace('.', '_')}.yaml"
    )
    with file_path.open(mode="r", encoding="utf-8") as file:
        endpoints = yaml.safe_load(file)

    if not isinstance(endpoints, list):
        raise MeterProtocolError(f"Invalid endpoint configuration in {file_path}")

    return endpoints


class XcelMeterClient:
    """Async client for the local Itron meter HTTP API."""

    def __init__(
        self,
        host: str,
        port: int,
        cert_path: str | Path,
        key_path: str | Path,
    ) -> None:
        self._host = host
        self._port = port
        self._ssl_context = create_ssl_context(cert_path, key_path)
        self._session: aiohttp.ClientSession | None = None

    @property
    def host(self) -> str:
        """Return the configured meter host."""
        return self._host

    async def async_open(self) -> None:
        """Open the HTTP session if it is not already open."""
        if self._session is not None:
            return

        connector = aiohttp.TCPConnector(
            ssl=self._ssl_context,
            limit=MAX_CONCURRENT_REQUESTS,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        )

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _async_get(self, path: str) -> str:
        """Fetch a path from the meter with a small retry loop."""
        await self.async_open()
        assert self._session is not None

        url = f"https://{self._host}:{self._port}{path}"
        last_error: Exception | None = None

        for attempt in range(REQUEST_ATTEMPTS):
            try:
                async with self._session.get(url) as response:
                    response.raise_for_status()
                    return await response.text()
            except aiohttp.ClientResponseError as err:
                if err.status >= 500 and attempt < REQUEST_ATTEMPTS - 1:
                    last_error = err
                else:
                    raise MeterProtocolError(
                        f"Meter returned HTTP {err.status} for {url}"
                    ) from err
            except TimeoutError as err:
                last_error = err
            except ssl.SSLError as err:
                raise MeterAuthError(
                    f"TLS handshake with the meter failed: {err}"
                ) from err
            except aiohttp.ClientError as err:
                if _is_tls_error(err):
                    raise MeterAuthError(
                        f"TLS handshake with the meter failed: {err}"
                    ) from err
                last_error = err

            if attempt < REQUEST_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])

        if last_error is not None and _is_tls_error(last_error):
            raise MeterAuthError(
                f"TLS handshake with the meter failed: {last_error}"
            ) from last_error

        raise MeterConnectionError(
            f"Unable to query {url}: {last_error}"
        ) from last_error

    async def async_get_endpoint(self, path: str) -> str:
        """Fetch an endpoint and return its XML response."""
        return await self._async_get(path)

    async def async_get_hardware_info(self) -> HardwareInfo:
        """Fetch the meter hardware information from /sdev/sdi."""
        response = await self._async_get("/sdev/sdi")

        try:
            root = ET.fromstring(response)
        except ET.ParseError as err:
            raise MeterProtocolError(
                "Meter returned invalid XML for /sdev/sdi"
            ) from err

        values: dict[str, str] = {}
        for name in ("lFDI", "swVer", "mfID"):
            element = root.find(f".//{IEEE_PREFIX}{name}")
            if element is None or element.text is None:
                raise MeterProtocolError(
                    f"Required field '{name}' missing from meter response"
                )
            values[name] = element.text.strip()

        return HardwareInfo(
            lfdi=values["lFDI"],
            manufacturer=values["mfID"],
            sw_version=values["swVer"],
        )
