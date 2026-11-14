"""Config flow: pair Home Assistant with a Centsible bridge like any household device."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    CentsibleClient,
    CentsibleConnectionError,
    CentsibleError,
    CentsiblePairingError,
    CentsibleRateLimitedError,
    PairResult,
    normalize_url,
    parse_pairing_input,
)
from .const import (
    CONF_ACCESS_EXPIRES_AT,
    CONF_ACCESS_TOKEN,
    CONF_BRIDGE_URL,
    CONF_BUDGET_ID,
    CONF_BUDGET_NAME,
    CONF_CF_ACCESS,
    CONF_CF_CLIENT_ID,
    CONF_CF_CLIENT_SECRET,
    CONF_CURRENCY,
    CONF_DEVICE_ID,
    CONF_MEMBER_NAME,
    CONF_PAIRING_CODE,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    cf = defaults.get(CONF_CF_ACCESS) or {}
    return vol.Schema(
        {
            vol.Required(CONF_PAIRING_CODE): str,
            vol.Optional(CONF_BRIDGE_URL, description={"suggested_value": defaults.get(CONF_BRIDGE_URL)}): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Required(CONF_CF_ACCESS): section(
                vol.Schema(
                    {
                        vol.Optional(
                            CONF_CF_CLIENT_ID, description={"suggested_value": cf.get(CONF_CF_CLIENT_ID)}
                        ): str,
                        vol.Optional(CONF_CF_CLIENT_SECRET): _SECRET,
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


class CentsibleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair with the bridge, then pick a budget."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: CentsibleClient | None = None
        self._paired: PairResult | None = None
        self._cf: tuple[str | None, str | None] = (None, None)
        self._budgets: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):  # type: ignore[no-untyped-def]
        return CentsibleOptionsFlow()

    async def _async_pair(self, user_input: dict[str, Any], fallback_url: str | None) -> dict[str, str]:
        """Redeem the code. Returns form errors (empty on success)."""
        try:
            info = parse_pairing_input(user_input[CONF_PAIRING_CODE])
        except ValueError:
            return {CONF_PAIRING_CODE: "invalid_link"}
        cf_in = user_input.get(CONF_CF_ACCESS) or {}
        url = info.bridge_url or (user_input.get(CONF_BRIDGE_URL) or "").strip() or fallback_url
        if not url:
            return {CONF_BRIDGE_URL: "url_required"}
        try:
            url = normalize_url(url)
        except ValueError:
            return {CONF_BRIDGE_URL: "invalid_url"}
        cf_id = info.cf_client_id or cf_in.get(CONF_CF_CLIENT_ID) or None
        cf_secret = info.cf_client_secret or cf_in.get(CONF_CF_CLIENT_SECRET) or None
        client = CentsibleClient(
            async_get_clientsession(self.hass), url, cf_client_id=cf_id, cf_client_secret=cf_secret
        )
        try:
            self._paired = await client.pair(info.code)
            budgets = await client.budgets()
        except CentsiblePairingError:
            return {"base": "invalid_code"}
        except CentsibleRateLimitedError:
            return {"base": "rate_limited"}
        except CentsibleConnectionError as err:
            _LOGGER.debug("Cannot reach bridge at %s: %s", url, err)
            return {"base": "cannot_connect"}
        except CentsibleError:
            _LOGGER.exception("Unexpected bridge response while pairing")
            return {"base": "unknown"}
        self._client = client
        self._cf = (cf_id, cf_secret)
        self._budgets = {b["id"]: b["name"] for b in budgets}
        return {}

    def _entry_data(self, budget_id: str) -> dict[str, Any]:
        assert self._client is not None and self._paired is not None
        tokens = self._client.tokens
        assert tokens is not None
        return {
            CONF_BRIDGE_URL: self._client.bridge_url,
            CONF_CF_CLIENT_ID: self._cf[0],
            CONF_CF_CLIENT_SECRET: self._cf[1],
            CONF_BUDGET_ID: budget_id,
            CONF_BUDGET_NAME: self._budgets.get(budget_id, budget_id),
            CONF_DEVICE_ID: self._paired.device_id,
            CONF_MEMBER_NAME: self._paired.member_name,
            CONF_ACCESS_TOKEN: tokens.access_token,
            CONF_REFRESH_TOKEN: tokens.refresh_token,
            CONF_ACCESS_EXPIRES_AT: tokens.expires_at.isoformat(),
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_pair(user_input, None)
            if not errors:
                if not self._budgets:
                    return self.async_abort(reason="no_budgets")
                if len(self._budgets) == 1:
                    return await self._async_create(next(iter(self._budgets)))
                return await self.async_step_budget()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_user_schema(user_input or {}), user_input or {}),
            errors=errors,
        )

    async def async_step_budget(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_create(user_input[CONF_BUDGET_ID])
        configured = {e.unique_id for e in self._async_current_entries()}
        options = [
            SelectOptionDict(value=bid, label=name) for bid, name in self._budgets.items() if bid not in configured
        ]
        if not options:
            return self.async_abort(reason="already_configured")
        return self.async_show_form(
            step_id="budget",
            data_schema=vol.Schema(
                {vol.Required(CONF_BUDGET_ID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
        )

    async def _async_create(self, budget_id: str) -> ConfigFlowResult:
        await self.async_set_unique_id(budget_id)
        self._abort_if_unique_id_configured()
        data = self._entry_data(budget_id)
        return self.async_create_entry(title=data[CONF_BUDGET_NAME], data=data)

    # -- reauth: the device was revoked, or its tokens lapsed --------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            # Keep the Access token we already have unless a new one is given.
            cf_in = user_input.get(CONF_CF_ACCESS) or {}
            user_input = {
                **user_input,
                CONF_CF_ACCESS: {
                    CONF_CF_CLIENT_ID: cf_in.get(CONF_CF_CLIENT_ID) or entry.data.get(CONF_CF_CLIENT_ID),
                    CONF_CF_CLIENT_SECRET: cf_in.get(CONF_CF_CLIENT_SECRET) or entry.data.get(CONF_CF_CLIENT_SECRET),
                },
            }
            errors = await self._async_pair(user_input, entry.data[CONF_BRIDGE_URL])
            if not errors:
                budget_id = entry.data[CONF_BUDGET_ID]
                if budget_id not in self._budgets:
                    errors = {"base": "budget_not_shared"}
                else:
                    return self.async_update_reload_and_abort(entry, data=self._entry_data(budget_id))
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_user_schema(entry.data),
            description_placeholders={"budget": entry.title},
            errors=errors,
        )


class CentsibleOptionsFlow(OptionsFlowWithReload):
    """How often to poll, and which currency to show."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            user_input[CONF_CURRENCY] = user_input[CONF_CURRENCY].strip().upper()
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_CURRENCY, default=options.get(CONF_CURRENCY, self.hass.config.currency)
                    ): TextSelector(),
                }
            ),
        )
