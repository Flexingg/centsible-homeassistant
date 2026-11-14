"""Sensors: account balances, this month's budget, envelopes, and the review inbox."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BUDGET_NAME, CONF_CURRENCY, DOMAIN
from .coordinator import CentsibleConfigEntry, CentsibleCoordinator, CentsibleData

PARALLEL_UPDATES = 0


def money(cents: int | None) -> Decimal | None:
    """Bridge amounts are integer cents."""
    return None if cents is None else Decimal(cents) / 100


@dataclass(frozen=True, kw_only=True)
class BudgetSensorDescription(SensorEntityDescription):
    """A sensor computed from the whole poll."""

    value_fn: Callable[[CentsibleData], Any]
    attrs_fn: Callable[[CentsibleData], dict[str, Any]] | None = None
    is_money: bool = True
    needs_month: bool = False


def _month(key: str, sign: int = 1) -> Callable[[CentsibleData], Decimal | None]:
    def value(data: CentsibleData) -> Decimal | None:
        if data.budget is None or key not in data.budget:
            return None
        return money(sign * data.budget[key])

    return value


def _overspent(data: CentsibleData) -> list[dict[str, Any]]:
    return [c for c in data.categories.values() if c["balance"] < 0 and not _hidden(c)]


def _hidden(cat: dict[str, Any]) -> bool:
    return bool(cat.get("hidden") or cat.get("groupHidden"))


def _net_worth(data: CentsibleData) -> Decimal:
    return money(sum(a["balance"] for a in data.accounts.values() if not a["closed"])) or Decimal(0)


BUDGET_SENSORS: tuple[BudgetSensorDescription, ...] = (
    BudgetSensorDescription(
        key="net_worth",
        translation_key="net_worth",
        state_class=SensorStateClass.TOTAL,
        value_fn=_net_worth,
    ),
    BudgetSensorDescription(
        key="to_budget",
        translation_key="to_budget",
        state_class=SensorStateClass.TOTAL,
        needs_month=True,
        value_fn=_month("toBudget"),
    ),
    BudgetSensorDescription(
        key="budgeted",
        translation_key="budgeted",
        state_class=SensorStateClass.TOTAL,
        needs_month=True,
        value_fn=_month("totalBudgeted"),
    ),
    BudgetSensorDescription(
        key="spent",
        translation_key="spent",
        state_class=SensorStateClass.TOTAL,
        needs_month=True,
        # Outflows are negative in the ledger; show spending as a positive amount.
        value_fn=_month("totalSpent", sign=-1),
    ),
    BudgetSensorDescription(
        key="income",
        translation_key="income",
        state_class=SensorStateClass.TOTAL,
        needs_month=True,
        value_fn=_month("totalIncome"),
    ),
    BudgetSensorDescription(
        key="available",
        translation_key="available",
        state_class=SensorStateClass.TOTAL,
        needs_month=True,
        value_fn=_month("totalBalance"),
    ),
    BudgetSensorDescription(
        key="overspent_categories",
        translation_key="overspent_categories",
        is_money=False,
        state_class=SensorStateClass.MEASUREMENT,
        needs_month=True,
        value_fn=lambda d: len(_overspent(d)),
        attrs_fn=lambda d: {
            "categories": [
                {"name": c["name"], "group": c["groupName"], "overspent": float(-money(c["balance"]))}  # type: ignore[operator]
                for c in _overspent(d)
            ]
        },
    ),
    BudgetSensorDescription(
        key="to_review",
        translation_key="to_review",
        is_money=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.review_total,
        attrs_fn=lambda d: {"more_than_500": d.review_more},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CentsibleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors, and more whenever an account or category appears."""
    coordinator = entry.runtime_data
    async_add_entities(CentsibleBudgetSensor(coordinator, d) for d in BUDGET_SENSORS)

    known_accounts: set[str] = set()
    known_categories: set[str] = set()

    @callback
    def add_new() -> None:
        data = coordinator.data
        new: list[SensorEntity] = []
        for acc_id, acc in data.accounts.items():
            if acc_id not in known_accounts and not acc["closed"]:
                known_accounts.add(acc_id)
                new.append(CentsibleAccountSensor(coordinator, acc_id))
        for cat_id, cat in data.categories.items():
            if cat_id not in known_categories and not _hidden(cat):
                known_categories.add(cat_id)
                new.append(CentsibleCategorySensor(coordinator, cat_id))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class CentsibleEntity(CoordinatorEntity[CentsibleCoordinator]):
    """Common device and naming for one budget."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CentsibleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        budget_id = coordinator.budget_id
        self._attr_unique_id = f"{budget_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, budget_id)},
            name=entry.data.get(CONF_BUDGET_NAME) or entry.title,
            manufacturer="Centsible",
            model="Actual budget",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _currency(self) -> str:
        return self.coordinator.config_entry.options.get(CONF_CURRENCY) or self.hass.config.currency


class CentsibleMoneySensor(CentsibleEntity, SensorEntity):
    """Shared money formatting."""

    _attr_suggested_display_precision = 2

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.device_class == SensorDeviceClass.MONETARY:
            return self._currency
        return super().native_unit_of_measurement


class CentsibleBudgetSensor(CentsibleMoneySensor):
    """A total for the budget or this month."""

    entity_description: BudgetSensorDescription

    def __init__(self, coordinator: CentsibleCoordinator, description: BudgetSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.is_money:
            self._attr_device_class = SensorDeviceClass.MONETARY
        else:
            self._attr_suggested_display_precision = None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.entity_description.needs_month and self.coordinator.data.budget is None:
            return False
        return self.entity_description.value_fn(self.coordinator.data) is not None

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs: dict[str, Any] = {}
        if self.entity_description.needs_month:
            attrs["month"] = self.coordinator.data.month
        if self.entity_description.attrs_fn:
            attrs.update(self.entity_description.attrs_fn(self.coordinator.data))
        return attrs or None


class CentsibleAccountSensor(CentsibleMoneySensor):
    """One account's balance."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_translation_key = "account"

    def __init__(self, coordinator: CentsibleCoordinator, account_id: str) -> None:
        super().__init__(coordinator, f"account_{account_id}")
        self._id = account_id
        self._attr_translation_placeholders = {"name": self._account["name"]}

    @property
    def _account(self) -> dict[str, Any]:
        return self.coordinator.data.accounts.get(self._id) or {}

    @property
    def available(self) -> bool:
        acc = self._account
        return super().available and bool(acc) and not acc.get("closed")

    @property
    def native_value(self) -> Decimal | None:
        return money(self._account.get("balance"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        acc = self._account
        return {
            "account_id": self._id,
            "off_budget": acc.get("offBudget"),
            "bank_sync": acc.get("syncSource"),
            "last_bank_sync": acc.get("lastSync"),
        }


class CentsibleCategorySensor(CentsibleMoneySensor):
    """What's left in one envelope this month."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_translation_key = "category"

    def __init__(self, coordinator: CentsibleCoordinator, category_id: str) -> None:
        super().__init__(coordinator, f"category_{category_id}")
        self._id = category_id
        self._attr_translation_placeholders = {"name": self._category["name"]}

    @property
    def _category(self) -> dict[str, Any]:
        return self.coordinator.data.categories.get(self._id) or {}

    @property
    def available(self) -> bool:
        cat = self._category
        return super().available and bool(cat) and not _hidden(cat)

    @property
    def native_value(self) -> Decimal | None:
        return money(self._category.get("balance"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cat = self._category
        spent = cat.get("spent")
        return {
            "category_id": self._id,
            "group": cat.get("groupName"),
            "month": self.coordinator.data.month,
            "budgeted": float(money(cat.get("budgeted")) or 0),
            "spent": float(-(money(spent) or 0)),
            "overspent": (cat.get("balance") or 0) < 0,
            "rollover_overspending": cat.get("carryover"),
        }
