"""Setup, sensors, token refresh and reauth."""

from __future__ import annotations

from http import HTTPStatus

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.centsible.const import CONF_ACCESS_EXPIRES_AT, CONF_REFRESH_TOKEN, DOMAIN

from .conftest import BUDGET, URL, mock_bridge, token_response


async def test_sensors(hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker) -> None:
    hass.config.currency = "USD"
    mock_bridge(aioclient_mock, dt_util.now().strftime("%Y-%m"))
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.LOADED

    # Cloudflare Access headers ride along on every request.
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["CF-Access-Client-Id"] == "cf-id"
    assert headers["Authorization"] == "Bearer access-0"

    checking = hass.states.get("sensor.family_checking_balance")
    assert float(checking.state) == 1234.56
    assert checking.attributes["unit_of_measurement"] == "USD"
    assert checking.attributes["bank_sync"] == "simpleFin"
    assert hass.states.get("sensor.family_old_savings_balance") is None  # closed

    assert float(hass.states.get("sensor.family_net_worth").state) == 1034.56
    assert float(hass.states.get("sensor.family_to_budget").state) == 50.0
    assert float(hass.states.get("sensor.family_spent_this_month").state) == 1500.0
    assert float(hass.states.get("sensor.family_income_this_month").state) == 4000.0

    groceries = hass.states.get("sensor.family_groceries_available")
    assert float(groceries.state) == 150.0
    assert groceries.attributes["spent"] == 450.0
    assert groceries.attributes["group"] == "Everyday"
    assert hass.states.get("sensor.family_secret_available") is None  # hidden
    assert hass.states.get("sensor.family_salary_available") is None  # income

    overspent = hass.states.get("sensor.family_overspent_categories")
    assert overspent.state == "1"
    assert overspent.attributes["categories"] == [{"name": "Dining", "group": "Everyday", "overspent": 25.0}]
    assert float(hass.states.get("sensor.family_transactions_to_review").state) == 3


async def test_missing_month_keeps_accounts(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    m = dt_util.now().strftime("%Y-%m")
    aioclient_mock.get(f"{URL}/v1/budgets/{BUDGET}/accounts", json={"items": []})
    aioclient_mock.get(
        f"{URL}/v1/budgets/{BUDGET}/months/{m}",
        status=404,
        json={"type": "x", "title": "Not found", "status": 404, "code": "not_found"},
        headers={"Content-Type": "application/problem+json"},
    )
    aioclient_mock.get(
        f"{URL}/v1/budgets/{BUDGET}/review",
        json={"items": [], "total": 0, "more": False, "since": "2026-11-01T00:00:00Z"},
    )
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.family_to_budget").state == "unavailable"
    assert float(hass.states.get("sensor.family_net_worth").state) == 0


async def test_expired_token_is_refreshed_and_saved(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    hass.config_entries.async_update_entry(
        mock_entry, data={**mock_entry.data, CONF_ACCESS_EXPIRES_AT: "2000-01-01T00:00:00+00:00"}
    )
    aioclient_mock.post(f"{URL}/v1/auth/refresh", json=token_response(1))
    mock_bridge(aioclient_mock, dt_util.now().strftime("%Y-%m"))
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    refreshes = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/v1/auth/refresh")]
    assert len(refreshes) == 1  # three parallel reads, one refresh
    assert refreshes[0][2] == {"refreshToken": "refresh-0"}
    assert mock_entry.data[CONF_REFRESH_TOKEN] == "refresh-1"
    # Saving tokens must not reload the entry.
    assert mock_entry.state is ConfigEntryState.LOADED


async def test_revoked_device_starts_reauth(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    problem = {"type": "x", "title": "Unauthorized", "status": 401, "code": "unauthorized"}
    ctype = {"Content-Type": "application/problem+json"}
    m = dt_util.now().strftime("%Y-%m")
    for path in ("accounts", f"months/{m}", "review"):
        aioclient_mock.get(
            f"{URL}/v1/budgets/{BUDGET}/{path}", status=HTTPStatus.UNAUTHORIZED, json=problem, headers=ctype
        )
    aioclient_mock.post(f"{URL}/v1/auth/refresh", status=HTTPStatus.UNAUTHORIZED, json=problem, headers=ctype)
    assert not await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_bridge_down_retries(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/v1/budgets/{BUDGET}/accounts", exc=TimeoutError())
    m = dt_util.now().strftime("%Y-%m")
    aioclient_mock.get(f"{URL}/v1/budgets/{BUDGET}/months/{m}", exc=TimeoutError())
    aioclient_mock.get(f"{URL}/v1/budgets/{BUDGET}/review", exc=TimeoutError())
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_remove_revokes_device(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_bridge(aioclient_mock, dt_util.now().strftime("%Y-%m"))
    aioclient_mock.post(f"{URL}/v1/auth/logout", status=204)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_remove(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert any(str(c[1]).endswith("/v1/auth/logout") for c in aioclient_mock.mock_calls)
