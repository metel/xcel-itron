"""Repair issues for the Xcel Itron integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, LAUNCHPAD_URL


def _issue_id(entry_id: str) -> str:
    return f"credentials_rejected_{entry_id}"


def async_create_credentials_issue(
    hass: HomeAssistant,
    entry_id: str,
    host: str,
    lfdi: str,
) -> None:
    """Create a repair issue when the meter rejects the client certificate."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="credentials_rejected",
        translation_placeholders={
            "host": host,
            "lfdi": lfdi,
            "launchpad_url": LAUNCHPAD_URL,
        },
    )


def async_delete_credentials_issue(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Remove the credentials repair issue."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))
