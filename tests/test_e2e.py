"""Against a real bridge and Actual server. Skipped unless CENTSIBLE_E2E_PAIRING_URI is set.

# in a Centsible checkout:
cd bridge && npx tsx test/support/e2e-server.ts 8787     # prints {"pairingUri": ...}
# here:
CENTSIBLE_E2E_PAIRING_URI='actualbridge://pair?...' pytest tests/test_e2e.py
"""

from __future__ import annotations

import os

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.centsible.const import CONF_PAIRING_CODE, CONF_REFRESH_TOKEN, DOMAIN

PAIRING_URI = os.environ.get("CENTSIBLE_E2E_PAIRING_URI")

pytestmark = pytest.mark.skipif(not PAIRING_URI, reason="CENTSIBLE_E2E_PAIRING_URI not set")


@pytest.fixture
def expected_lingering_timers() -> bool:
    return True


@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_pair_and_read_real_budget(hass: HomeAssistant, socket_enabled: None) -> None:
    hass.config.currency = "USD"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: PAIRING_URI, "cloudflare_access": {}}
    )
    if result["type"] is FlowResultType.FORM and result["step_id"] == "budget":
        options = result["data_schema"].schema["budget_id"].config["options"]
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"budget_id": options[0]["value"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    states = {e.entity_id: hass.states.get(e.entity_id) for e in entities}
    for entity_id, state in sorted(states.items()):
        print(f"{entity_id:60} {state.state:>14} {state.attributes.get('unit_of_measurement', '')}")
    accounts = [s for i, s in states.items() if i.endswith("_balance")]
    assert accounts and all(s.state not in ("unavailable", "unknown") for s in accounts)
    assert states[next(i for i in states if i.endswith("_net_worth"))].state not in ("unavailable", "unknown")

    # A forced token rotation keeps working, and the new refresh token is saved.
    coordinator = entry.runtime_data
    before = entry.data[CONF_REFRESH_TOKEN]
    await coordinator.client._access_token(force=True)
    assert entry.data[CONF_REFRESH_TOKEN] != before
    await coordinator.async_refresh()
    assert coordinator.last_update_success

    # Deleting the integration revokes the device on the bridge.
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
