"""Centsible: your household budget in Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import CentsibleClient, CentsibleError, Tokens
from .const import (
    CONF_ACCESS_EXPIRES_AT,
    CONF_ACCESS_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CF_CLIENT_ID,
    CONF_CF_CLIENT_SECRET,
    CONF_REFRESH_TOKEN,
)
from .coordinator import CentsibleConfigEntry, CentsibleCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


def build_client(hass: HomeAssistant, entry: CentsibleConfigEntry) -> CentsibleClient:
    """A client for this entry that saves every rotated token back into the entry."""
    data = entry.data
    expires = dt_util.parse_datetime(data.get(CONF_ACCESS_EXPIRES_AT) or "")
    tokens = Tokens(
        access_token=data.get(CONF_ACCESS_TOKEN) or "",
        refresh_token=data[CONF_REFRESH_TOKEN],
        expires_at=expires or dt_util.utc_from_timestamp(0),
    )

    def save(new: Tokens) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: new.access_token,
                CONF_REFRESH_TOKEN: new.refresh_token,
                CONF_ACCESS_EXPIRES_AT: new.expires_at.isoformat(),
            },
        )

    return CentsibleClient(
        async_get_clientsession(hass),
        data[CONF_BRIDGE_URL],
        tokens=tokens,
        cf_client_id=data.get(CONF_CF_CLIENT_ID),
        cf_client_secret=data.get(CONF_CF_CLIENT_SECRET),
        on_tokens=save,
    )


async def async_setup_entry(hass: HomeAssistant, entry: CentsibleConfigEntry) -> bool:
    """Set up a budget from a config entry."""
    coordinator = CentsibleCoordinator(hass, entry, build_client(hass, entry))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No update listener: saving rotated tokens updates the entry, and the options flow
    # reloads by itself.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CentsibleConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: CentsibleConfigEntry) -> None:
    """Revoke this device on the bridge when the integration is deleted."""
    try:
        await build_client(hass, entry).logout()
    except CentsibleError as err:
        _LOGGER.info("Could not revoke the Home Assistant device on the bridge: %s", err)
