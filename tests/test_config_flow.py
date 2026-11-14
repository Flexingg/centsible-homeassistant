"""Pairing, budget choice, reauth and options."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.centsible.api import parse_pairing_input
from custom_components.centsible.const import (
    CONF_BRIDGE_URL,
    CONF_BUDGET_ID,
    CONF_CF_CLIENT_SECRET,
    CONF_CURRENCY,
    CONF_PAIRING_CODE,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import BUDGET, URL, token_response

PROBLEM = {"Content-Type": "application/problem+json"}


def test_parse_pairing_link() -> None:
    info = parse_pairing_input(
        " actualbridge://pair?u=https%3A%2F%2Fbudget-api.example.com&c=ABCD-EFGH&cfid=id.access&cfsecret=s3cret "
    )
    assert (info.bridge_url, info.code, info.cf_client_id, info.cf_client_secret) == (
        URL,
        "ABCD-EFGH",
        "id.access",
        "s3cret",
    )
    assert parse_pairing_input("ABCD-EFGH").bridge_url is None


async def test_pair_with_link(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{URL}/v1/auth/pair", json=token_response(1))
    aioclient_mock.get(f"{URL}/v1/budgets", json={"items": [{"id": BUDGET, "name": "Family", "encrypted": False}]})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PAIRING_CODE: "actualbridge://pair?u=https%3A%2F%2Fbudget-api.example.com&c=ABCD-EFGH&cfid=cid&cfsecret=csec",
            "cloudflare_access": {},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Family"
    assert result["data"][CONF_BRIDGE_URL] == URL
    assert result["data"][CONF_CF_CLIENT_SECRET] == "csec"
    assert result["data"][CONF_REFRESH_TOKEN] == "refresh-1"
    assert result["result"].unique_id == BUDGET

    _, _, body, headers = aioclient_mock.mock_calls[0]
    assert body == {"code": "ABCD-EFGH", "deviceName": "Home Assistant", "platform": "other"}
    assert headers["CF-Access-Client-Id"] == "cid"


async def test_pair_with_code_and_choose_budget(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{URL}/v1/auth/pair", json=token_response(1))
    aioclient_mock.get(
        f"{URL}/v1/budgets",
        json={
            "items": [
                {"id": BUDGET, "name": "Family", "encrypted": False},
                {"id": "b2", "name": "Business", "encrypted": False},
            ]
        },
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PAIRING_CODE: "ABCD-EFGH", CONF_BRIDGE_URL: "budget-api.example.com/", "cloudflare_access": {}},
    )
    assert result["step_id"] == "budget"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_BUDGET_ID: "b2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Business"
    assert result["data"][CONF_BRIDGE_URL] == URL


async def test_code_needs_url(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "ABCD-EFGH", "cloudflare_access": {}}
    )
    assert result["errors"] == {CONF_BRIDGE_URL: "url_required"}


async def test_bad_code(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        f"{URL}/v1/auth/pair",
        status=401,
        json={"type": "x", "title": "Invalid", "status": 401, "code": "unauthorized"},
        headers=PROBLEM,
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "WRONG-CODE", CONF_BRIDGE_URL: URL, "cloudflare_access": {}}
    )
    assert result["errors"] == {"base": "invalid_code"}


async def test_cloudflare_login_page(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{URL}/v1/auth/pair", status=302, text="<html>", headers={"Content-Type": "text/html"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "ABCD-EFGH", CONF_BRIDGE_URL: URL, "cloudflare_access": {}}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{URL}/v1/auth/pair", json=token_response(1))
    aioclient_mock.get(f"{URL}/v1/budgets", json={"items": [{"id": BUDGET, "name": "Family", "encrypted": False}]})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "ABCD-EFGH", CONF_BRIDGE_URL: URL, "cloudflare_access": {}}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{URL}/v1/auth/pair", json=token_response(7))
    aioclient_mock.get(f"{URL}/v1/budgets", json={"items": [{"id": BUDGET, "name": "Family", "encrypted": False}]})
    result = await mock_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "NEWC-ODE1", "cloudflare_access": {}}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_entry.data[CONF_REFRESH_TOKEN] == "refresh-7"
    # The saved Access token is reused when none is typed.
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["CF-Access-Client-Secret"] == "cf-secret"


async def test_options(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30.0, CONF_CURRENCY: " eur "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options == {CONF_SCAN_INTERVAL: 30, CONF_CURRENCY: "EUR"}
