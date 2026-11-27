"""Tests for the Xcel Itron data update coordinator."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.xcel_itron.const import DOMAIN
from custom_components.xcel_itron.coordinator import XcelItronCoordinator
from custom_components.xcel_itron.meter import (
    MeterAuthError,
    MeterConnectionError,
    normalize_endpoints,
)
from custom_components.xcel_itron.sensor import build_sensor_specs

ENDPOINT_DEFINITIONS = [
    {
        "Demand": {
            "url": "/demand",
            "tags": {
                "value": {
                    "entity_type": "sensor",
                    "device_class": "power",
                    "unit_of_measurement": "W",
                    "state_class": "measurement",
                }
            },
        }
    },
    {
        "Summation": {
            "url": "/summation",
            "tags": {
                "value": {
                    "entity_type": "sensor",
                    "device_class": "energy",
                    "unit_of_measurement": "Wh",
                    "state_class": "total_increasing",
                }
            },
        }
    },
]

ENDPOINTS = normalize_endpoints(ENDPOINT_DEFINITIONS)

RESPONSES = {
    "/demand": (
        '<Reading xmlns="urn:ieee:std:2030.5:ns"><value>1234</value></Reading>'
    ),
    "/summation": (
        '<Reading xmlns="urn:ieee:std:2030.5:ns"><value>123456</value></Reading>'
    ),
}


class FakeClient:
    """Minimal stand-in for XcelMeterClient."""

    host = "192.168.1.50"

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.errors = errors or {}

    async def async_get_endpoint(self, path: str) -> str:
        if path in self.errors:
            raise self.errors[path]
        return self.responses[path]


def _coordinator(hass, client: FakeClient) -> XcelItronCoordinator:
    return XcelItronCoordinator(
        hass,
        client,  # type: ignore[arg-type]
        ENDPOINTS,
        build_sensor_specs(ENDPOINTS),
        timedelta(seconds=30),
        "entry",
        "CLIENTLFDI",
    )


@pytest.mark.asyncio
async def test_update_data(hass) -> None:
    coordinator = _coordinator(hass, FakeClient(RESPONSES))

    data = await coordinator._async_update_data()

    assert data == {
        "/demand#value": 1234.0,
        "/summation#value": 123456.0,
    }


@pytest.mark.asyncio
async def test_partial_failure_keeps_other_values(hass) -> None:
    coordinator = _coordinator(
        hass,
        FakeClient(
            RESPONSES,
            {"/summation": MeterConnectionError("boom")},
        ),
    )
    coordinator.data = {"/summation#value": 1.0}

    data = await coordinator._async_update_data()

    assert data["/demand#value"] == 1234.0
    assert data["/summation#value"] == 1.0


@pytest.mark.asyncio
async def test_all_endpoints_fail(hass) -> None:
    coordinator = _coordinator(
        hass,
        FakeClient(
            {},
            {
                "/demand": MeterConnectionError("demand"),
                "/summation": MeterConnectionError("summation"),
            },
        ),
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_auth_error_creates_and_clears_issue(hass) -> None:
    coordinator = _coordinator(
        hass,
        FakeClient(RESPONSES, {"/demand": MeterAuthError("rejected")}),
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    registry = ir.async_get(hass)
    issue_id = "credentials_rejected_entry"
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    coordinator.client = FakeClient(RESPONSES)  # type: ignore[assignment]
    await coordinator._async_update_data()

    assert registry.async_get_issue(DOMAIN, issue_id) is None
