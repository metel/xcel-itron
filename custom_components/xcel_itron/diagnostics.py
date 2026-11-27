"""Diagnostics support for the Xcel Itron integration."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CERT_PATH,
    CONF_HOST,
    CONF_KEY_PATH,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator

    return {
        "entry": {
            "host": entry.data.get(CONF_HOST),
            "port": entry.data.get(CONF_PORT),
            "name": entry.data.get(CONF_NAME),
            "cert_path": entry.data.get(CONF_CERT_PATH),
            "key_path": entry.data.get(CONF_KEY_PATH),
            "scan_interval": entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            ),
        },
        "hardware": asdict(runtime.hardware),
        "client_lfdi": runtime.client_lfdi,
        "endpoint_count": len(runtime.endpoints),
        "sensor_count": len(runtime.specs),
        "last_update_success": coordinator.last_update_success,
        "values": {
            key: _serialize(value) for key, value in (coordinator.data or {}).items()
        },
    }
