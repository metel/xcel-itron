"""Tests for setting up and unloading the integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xcel_itron.certificates import (
    generate_client_certificate,
)
from custom_components.xcel_itron.const import (
    CONF_CERT_PATH,
    CONF_HOST,
    CONF_KEY_PATH,
    CONF_NAME,
    CONF_PORT,
    DOMAIN,
)
from custom_components.xcel_itron.meter import (
    HardwareInfo,
    MeterConnectionError,
)

READING_XML = (
    '<Reading xmlns="urn:ieee:std:2030.5:ns">'
    "<timePeriod><start>2024-01-01T00:00:00Z</start></timePeriod>"
    "<value>1234</value></Reading>"
)


def _entry(hass) -> MockConfigEntry:
    cert_pem, key_pem, _ = generate_client_certificate()
    cert_path = Path(hass.config.path("xcel_itron/cert.pem"))
    key_path = Path(hass.config.path("xcel_itron/key.pem"))
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)

    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Meter",
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 8081,
            CONF_NAME: "Test Meter",
            CONF_CERT_PATH: "xcel_itron/cert.pem",
            CONF_KEY_PATH: "xcel_itron/key.pem",
        },
    )


@pytest.mark.asyncio
async def test_setup_and_unload(hass) -> None:
    entry = _entry(hass)
    entry.add_to_hass(hass)

    hardware = HardwareInfo(lfdi="METERLFDI", manufacturer="Itron", sw_version="3.2.50")

    with (
        patch(
            "custom_components.xcel_itron.XcelMeterClient.async_get_hardware_info",
            new=AsyncMock(return_value=hardware),
        ),
        patch(
            "custom_components.xcel_itron.XcelMeterClient.async_get_endpoint",
            new=AsyncMock(return_value=READING_XML),
        ),
        patch(
            "custom_components.xcel_itron.XcelMeterClient.async_close",
            new=AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.hardware.lfdi == "METERLFDI"
        assert len(entry.runtime_data.specs) == 37

        state = hass.states.get("sensor.test_meter_instantaneous_demand_value")
        assert state is not None
        assert state.state == "1234.0"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_setup_retries_when_meter_is_unreachable(hass) -> None:
    entry = _entry(hass)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.xcel_itron.XcelMeterClient.async_get_hardware_info",
        new=AsyncMock(side_effect=MeterConnectionError("boom")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
