"""Data update coordinator for the Xcel Itron integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any
from xml.etree.ElementTree import ParseError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, MAX_CONCURRENT_REQUESTS
from .meter import (
    EndpointConfig,
    MeterAuthError,
    XcelMeterClient,
    endpoint_data_key,
    parse_endpoint_response,
)
from .repairs import (
    async_create_credentials_issue,
    async_delete_credentials_issue,
)
from .sensor import SensorSpec, convert_value

_LOGGER = logging.getLogger(__name__)


class XcelItronCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll all configured meter endpoints."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: XcelMeterClient,
        endpoints: list[EndpointConfig],
        specs: list[SensorSpec],
        update_interval: timedelta,
        entry_id: str,
        client_lfdi: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self._endpoints = endpoints
        self._specs = {spec.data_key: spec for spec in specs}
        self._entry_id = entry_id
        self._client_lfdi = client_lfdi

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch every endpoint and convert the readings."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async def fetch(endpoint: EndpointConfig) -> str:
            async with semaphore:
                return await self.client.async_get_endpoint(endpoint.url)

        results = await asyncio.gather(
            *(fetch(endpoint) for endpoint in self._endpoints),
            return_exceptions=True,
        )

        values: dict[str, Any] = {}
        errors: list[tuple[str, BaseException]] = []
        auth_error: MeterAuthError | None = None

        for endpoint, result in zip(self._endpoints, results, strict=True):
            if isinstance(result, MeterAuthError):
                auth_error = result
                errors.append((endpoint.name, result))
                continue
            if isinstance(result, BaseException):
                errors.append((endpoint.name, result))
                continue

            try:
                readings = parse_endpoint_response(result, endpoint)
            except ParseError as err:
                errors.append((endpoint.name, err))
                continue

            for tag, raw_value in readings.items():
                data_key = endpoint_data_key(endpoint.url, tag)
                spec = self._specs.get(data_key)
                if spec is None:
                    continue
                converted = convert_value(raw_value, spec)
                if converted is not None:
                    values[data_key] = converted

        if auth_error is not None:
            async_create_credentials_issue(
                self.hass,
                self._entry_id,
                self.client.host,
                self._client_lfdi,
            )
            raise UpdateFailed(
                f"The meter rejected the client certificate: {auth_error}"
            ) from auth_error

        # The TLS connection succeeded, so any previous certificate rejection
        # no longer applies.
        async_delete_credentials_issue(self.hass, self._entry_id)

        if errors:
            _LOGGER.warning(
                "Failed to update %s of %s meter endpoints: %s",
                len(errors),
                len(self._endpoints),
                ", ".join(name for name, _ in errors),
            )
            if not values:
                raise UpdateFailed(
                    f"All meter endpoints failed: {errors[0][1]}"
                ) from errors[0][1]

            # Keep the previous values for endpoints that failed this cycle.
            if self.data:
                for key, value in self.data.items():
                    values.setdefault(key, value)

        return values
