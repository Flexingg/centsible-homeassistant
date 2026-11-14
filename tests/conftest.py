"""Fixtures: a fake bridge served by aioclient_mock."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.centsible.const import (
    CONF_ACCESS_EXPIRES_AT,
    CONF_ACCESS_TOKEN,
    CONF_BRIDGE_URL,
    CONF_BUDGET_ID,
    CONF_BUDGET_NAME,
    CONF_CF_CLIENT_ID,
    CONF_CF_CLIENT_SECRET,
    CONF_DEVICE_ID,
    CONF_MEMBER_NAME,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

URL = "https://budget-api.example.com"
BUDGET = "budget-1"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
def json_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bridge labels JSON as JSON (the client relies on it to spot Cloudflare's login page)."""
    original = AiohttpClientMocker.request

    def request(self, method, url, *args, json=None, headers=None, **kwargs):  # type: ignore[no-untyped-def]
        if json is not None and not headers:
            headers = {"Content-Type": "application/json"}
        return original(self, method, url, *args, json=json, headers=headers, **kwargs)

    monkeypatch.setattr(AiohttpClientMocker, "request", request)


def token_response(n: int = 1) -> dict[str, Any]:
    return {
        "accessToken": f"access-{n}",
        "refreshToken": f"refresh-{n}",
        "expiresIn": 3600,
        "member": {
            "id": "m1",
            "displayName": "Home Assistant",
            "role": "viewer",
            "disabled": False,
            "budgetIds": [BUDGET],
        },
        "device": {"id": "dev-1", "name": "Home Assistant", "platform": "other", "createdAt": "2026-11-14T00:00:00Z"},
    }


ACCOUNTS = {
    "items": [
        {
            "id": "a1",
            "name": "Checking",
            "offBudget": False,
            "closed": False,
            "balance": 123456,
            "syncSource": "simpleFin",
            "lastSync": None,
        },
        {"id": "a2", "name": "Visa", "offBudget": False, "closed": False, "balance": -20000},
        {"id": "a3", "name": "Old savings", "offBudget": False, "closed": True, "balance": 0},
    ]
}


def month(m: str) -> dict[str, Any]:
    return {
        "month": m,
        "budgetType": "envelope",
        "toBudget": 5000,
        "incomeAvailable": 0,
        "lastMonthOverspent": 0,
        "forNextMonth": 0,
        "fromLastMonth": 0,
        "totalBudgeted": 300000,
        "totalIncome": 400000,
        "totalSpent": -150000,
        "totalBalance": 150000,
        "groups": [
            {
                "id": "g1",
                "name": "Everyday",
                "isIncome": False,
                "hidden": False,
                "budgeted": 0,
                "spent": 0,
                "balance": 0,
                "received": 0,
                "categories": [
                    {
                        "id": "c1",
                        "name": "Groceries",
                        "hidden": False,
                        "budgeted": 60000,
                        "spent": -45000,
                        "balance": 15000,
                        "received": 0,
                        "carryover": False,
                    },
                    {
                        "id": "c2",
                        "name": "Dining",
                        "hidden": False,
                        "budgeted": 10000,
                        "spent": -12500,
                        "balance": -2500,
                        "received": 0,
                        "carryover": False,
                    },
                    {
                        "id": "c3",
                        "name": "Secret",
                        "hidden": True,
                        "budgeted": 0,
                        "spent": 0,
                        "balance": 0,
                        "received": 0,
                        "carryover": False,
                    },
                ],
            },
            {
                "id": "g2",
                "name": "Income",
                "isIncome": True,
                "hidden": False,
                "budgeted": 0,
                "spent": 0,
                "balance": 0,
                "received": 400000,
                "categories": [
                    {
                        "id": "i1",
                        "name": "Salary",
                        "hidden": False,
                        "budgeted": 0,
                        "spent": 0,
                        "balance": 0,
                        "received": 400000,
                        "carryover": False,
                    }
                ],
            },
        ],
    }


def mock_bridge(aioclient_mock: AiohttpClientMocker, month_str: str, *, review_total: int = 3) -> None:
    aioclient_mock.get(f"{URL}/v1/budgets/{BUDGET}/accounts", json=ACCOUNTS)
    aioclient_mock.get(f"{URL}/v1/budgets/{BUDGET}/months/{month_str}", json=month(month_str))
    aioclient_mock.get(
        f"{URL}/v1/budgets/{BUDGET}/review",
        json={"items": [], "total": review_total, "more": False, "since": "2026-11-01T00:00:00Z"},
    )


@pytest.fixture
def mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BUDGET,
        title="Family",
        data={
            CONF_BRIDGE_URL: URL,
            CONF_CF_CLIENT_ID: "cf-id",
            CONF_CF_CLIENT_SECRET: "cf-secret",
            CONF_BUDGET_ID: BUDGET,
            CONF_BUDGET_NAME: "Family",
            CONF_DEVICE_ID: "dev-1",
            CONF_MEMBER_NAME: "Home Assistant",
            CONF_ACCESS_TOKEN: "access-0",
            CONF_REFRESH_TOKEN: "refresh-0",
            CONF_ACCESS_EXPIRES_AT: "2999-01-01T00:00:00+00:00",
        },
    )
    entry.add_to_hass(hass)
    return entry
