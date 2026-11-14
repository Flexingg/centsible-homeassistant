"""Constants for the Centsible integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "centsible"

CONF_BRIDGE_URL: Final = "bridge_url"
CONF_PAIRING_CODE: Final = "pairing_code"
CONF_CF_ACCESS: Final = "cloudflare_access"
CONF_CF_CLIENT_ID: Final = "cf_client_id"
CONF_CF_CLIENT_SECRET: Final = "cf_client_secret"
CONF_BUDGET_ID: Final = "budget_id"
CONF_BUDGET_NAME: Final = "budget_name"
CONF_DEVICE_ID: Final = "device_id"
CONF_MEMBER_NAME: Final = "member_name"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_ACCESS_EXPIRES_AT: Final = "access_expires_at"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_CURRENCY: Final = "currency"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 3600

DEVICE_NAME: Final = "Home Assistant"
PAIRING_SCHEME: Final = "actualbridge"

REQUEST_TIMEOUT: Final = 30
# Refresh the access token this long before the bridge says it expires.
TOKEN_SKEW: Final = timedelta(minutes=2)
