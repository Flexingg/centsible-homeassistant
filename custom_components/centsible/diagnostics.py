"""Diagnostics, with secrets removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CF_CLIENT_ID,
    CONF_CF_CLIENT_SECRET,
    CONF_REFRESH_TOKEN,
)
from .coordinator import CentsibleConfigEntry

TO_REDACT = {CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_CF_CLIENT_ID, CONF_CF_CLIENT_SECRET, CONF_BRIDGE_URL}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: CentsibleConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "data": {
            "month": data.month,
            "accounts": len(data.accounts),
            "categories": len(data.categories),
            "has_budget_month": data.budget is not None,
            "review_total": data.review_total,
        }
        if data
        else None,
    }
