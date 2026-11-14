"""Polls the bridge for one budget."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    CentsibleApiError,
    CentsibleAuthError,
    CentsibleClient,
    CentsibleConnectionError,
)
from .const import CONF_BUDGET_ID, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type CentsibleConfigEntry = ConfigEntry[CentsibleCoordinator]


@dataclass(slots=True)
class CentsibleData:
    """One poll's worth of budget data. Money is in cents, as the bridge sends it."""

    month: str
    accounts: dict[str, dict[str, Any]]
    budget: dict[str, Any] | None
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)
    review_total: int | None = None
    review_more: bool = False


class CentsibleCoordinator(DataUpdateCoordinator[CentsibleData]):
    """Fetches accounts, this month's budget and the review inbox together."""

    config_entry: CentsibleConfigEntry

    def __init__(self, hass: HomeAssistant, entry: CentsibleConfigEntry, client: CentsibleClient) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.budget_id: str = entry.data[CONF_BUDGET_ID]

    async def _async_update_data(self) -> CentsibleData:
        month = dt_util.now().strftime("%Y-%m")
        accounts, budget, review = await asyncio.gather(
            self.client.accounts(self.budget_id),
            self.client.budget_month(self.budget_id, month),
            self.client.review_inbox(self.budget_id),
            return_exceptions=True,
        )
        for result in (accounts, budget, review):
            if isinstance(result, CentsibleAuthError):
                raise ConfigEntryAuthFailed(str(result)) from result
        if isinstance(accounts, BaseException):
            raise _update_failed(accounts)
        # The month and the inbox are extras: keep going without them.
        if isinstance(budget, BaseException):
            _LOGGER.debug("Budget month unavailable: %s", budget)
            budget = None
        if isinstance(review, BaseException):
            _LOGGER.debug("Review inbox unavailable: %s", review)
            review = None

        categories: dict[str, dict[str, Any]] = {}
        if budget:
            for group in budget.get("groups", []):
                if group.get("isIncome"):
                    continue
                for cat in group.get("categories", []):
                    categories[cat["id"]] = {
                        **cat,
                        "groupName": group["name"],
                        "groupHidden": group.get("hidden", False),
                    }

        return CentsibleData(
            month=month,
            accounts={a["id"]: a for a in accounts},
            budget=budget,
            categories=categories,
            review_total=review.get("total") if review else None,
            review_more=bool(review.get("more")) if review else False,
        )


def _update_failed(err: BaseException) -> UpdateFailed:
    if isinstance(err, CentsibleApiError) and err.code == "budget_encrypted":
        return UpdateFailed(f"The budget is end-to-end encrypted: {err.detail}")
    if isinstance(err, CentsibleApiError) and err.status == 403:
        return UpdateFailed("This member can't open the budget any more")
    if isinstance(err, (CentsibleConnectionError, CentsibleApiError)):
        return UpdateFailed(str(err))
    return UpdateFailed(f"Unexpected error: {err!r}")
