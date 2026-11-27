"""Tests for the Xcel Itron config flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xcel_itron.certificates import (
    generate_client_certificate,
)
from custom_components.xcel_itron.const import (
    CONF_CERT_PATH,
    CONF_CREDENTIAL_MODE,
    CONF_HOST,
    CONF_KEY_PATH,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CREDENTIAL_MODE_EXISTING,
    CREDENTIAL_MODE_GENERATE,
    DOMAIN,
)
from custom_components.xcel_itron.meter import (
    HardwareInfo,
    MeterConnectionError,
)

USER_INPUT = {
    CONF_HOST: "192.168.1.50",
    CONF_PORT: 8081,
    CONF_NAME: "Test Meter",
    CONF_CREDENTIAL_MODE: CREDENTIAL_MODE_GENERATE,
}


def _write_cert_files(hass) -> tuple[Path, Path, str]:
    cert_pem, key_pem, lfdi = generate_client_certificate()
    cert_path = Path(hass.config.path("xcel_itron/cert.pem"))
    key_path = Path(hass.config.path("xcel_itron/key.pem"))
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    return cert_path, key_path, lfdi


@pytest.mark.asyncio
async def test_user_form(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_generate_flow(hass) -> None:
    cert_pem, key_pem, lfdi = generate_client_certificate()

    with (
        patch(
            "custom_components.xcel_itron.config_flow.generate_client_certificate",
            return_value=(cert_pem, key_pem, lfdi),
        ),
        patch(
            "custom_components.xcel_itron.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

        assert result["type"] == "form"
        assert result["step_id"] == "lfdi"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == "create_entry"
    assert result["data"][CONF_CERT_PATH] == f"xcel_itron/{lfdi}/cert.pem"
    assert result["data"][CONF_KEY_PATH] == f"xcel_itron/{lfdi}/key.pem"
    assert Path(hass.config.path(result["data"][CONF_CERT_PATH])).is_file()


@pytest.mark.asyncio
async def test_existing_certificate_flow(hass) -> None:
    _write_cert_files(hass)
    hardware = HardwareInfo(lfdi="METERLFDI", manufacturer="Itron", sw_version="3.2.50")

    with (
        patch(
            "custom_components.xcel_itron.config_flow.XcelMeterClient.async_get_hardware_info",
            new=AsyncMock(return_value=hardware),
        ),
        patch(
            "custom_components.xcel_itron.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **USER_INPUT,
                CONF_CREDENTIAL_MODE: CREDENTIAL_MODE_EXISTING,
            },
        )

        assert result["step_id"] == "existing"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_CERT_PATH: "xcel_itron/cert.pem",
                CONF_KEY_PATH: "xcel_itron/key.pem",
            },
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_CERT_PATH] == "xcel_itron/cert.pem"


@pytest.mark.asyncio
async def test_existing_certificate_invalid_path(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **USER_INPUT,
            CONF_CREDENTIAL_MODE: CREDENTIAL_MODE_EXISTING,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CERT_PATH: "xcel_itron/missing.pem",
            CONF_KEY_PATH: "xcel_itron/missing-key.pem",
        },
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_certificate"


@pytest.mark.asyncio
async def test_existing_certificate_cannot_connect(hass) -> None:
    _write_cert_files(hass)

    with patch(
        "custom_components.xcel_itron.config_flow.XcelMeterClient.async_get_hardware_info",
        new=AsyncMock(side_effect=MeterConnectionError("boom")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **USER_INPUT,
                CONF_CREDENTIAL_MODE: CREDENTIAL_MODE_EXISTING,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_CERT_PATH: "xcel_itron/cert.pem",
                CONF_KEY_PATH: "xcel_itron/key.pem",
            },
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_duplicate_lfdi(hass) -> None:
    cert_pem, key_pem, lfdi = generate_client_certificate()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=lfdi,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 8081,
            CONF_NAME: "Test Meter",
            CONF_CERT_PATH: "xcel_itron/cert.pem",
            CONF_KEY_PATH: "xcel_itron/key.pem",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.xcel_itron.config_flow.generate_client_certificate",
        return_value=(cert_pem, key_pem, lfdi),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_zeroconf_discovery(hass) -> None:
    discovery = Mock()
    discovery.ip_address = "192.168.1.50"
    discovery.port = 8081
    discovery.name = "Xcel Meter._smartenergy._tcp.local."

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "zeroconf"},
        data=discovery,
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"

    defaults = result["data_schema"]({})
    assert defaults[CONF_HOST] == "192.168.1.50"
    assert defaults[CONF_PORT] == 8081


@pytest.mark.asyncio
async def test_options_flow(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60}
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_SCAN_INTERVAL: 60}


@pytest.mark.asyncio
async def test_reconfigure_form(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 8081,
            CONF_NAME: "Test Meter",
            CONF_CERT_PATH: "xcel_itron/cert.pem",
            CONF_KEY_PATH: "xcel_itron/key.pem",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"

    defaults = result["data_schema"]({})
    assert defaults[CONF_HOST] == "192.168.1.50"
    assert defaults[CONF_PORT] == 8081
