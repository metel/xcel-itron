"""Tests for Xcel Itron diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.xcel_itron.const import (
    CONF_CERT_PATH,
    CONF_HOST,
    CONF_KEY_PATH,
    CONF_NAME,
    CONF_PORT,
)
from custom_components.xcel_itron.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.xcel_itron.meter import HardwareInfo


@pytest.mark.asyncio
async def test_diagnostics(hass) -> None:
    coordinator = SimpleNamespace(
        last_update_success=True,
        data={
            "/upt/1/mr/1/r#value": 1234.0,
            "/upt/1/mr/2/rs/1/r/1#timePeriod_start": datetime(2024, 1, 1, tzinfo=UTC),
        },
    )
    runtime = SimpleNamespace(
        coordinator=coordinator,
        hardware=HardwareInfo(
            lfdi="METERLFDI",
            manufacturer="Itron",
            sw_version="3.2.50",
        ),
        client_lfdi="CLIENTLFDI",
        endpoints=[{}],
        specs=[object()],
    )
    entry = SimpleNamespace(
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 8081,
            CONF_NAME: "Test Meter",
            CONF_CERT_PATH: "xcel_itron/cert.pem",
            CONF_KEY_PATH: "xcel_itron/key.pem",
        },
        options={},
        runtime_data=runtime,
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"][CONF_HOST] == "192.168.1.50"
    assert diagnostics["hardware"]["sw_version"] == "3.2.50"
    assert diagnostics["client_lfdi"] == "CLIENTLFDI"
    assert diagnostics["values"]["/upt/1/mr/1/r#value"] == 1234.0
    assert (
        diagnostics["values"]["/upt/1/mr/2/rs/1/r/1#timePeriod_start"]
        == "2024-01-01T00:00:00+00:00"
    )
    assert "PRIVATE KEY" not in str(diagnostics)
