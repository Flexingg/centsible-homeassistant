"""Client for the Centsible bridge (contract v1).

The bridge rotates refresh tokens on every use, and treats a refresh token that was
already rotated as stolen: it revokes the whole device. So:

* refreshes are serialized behind a lock, and
* every new token pair is handed to ``on_tokens`` straight away so it can be saved
  before anything else uses it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import DEVICE_NAME, PAIRING_SCHEME, REQUEST_TIMEOUT, TOKEN_SKEW


class CentsibleError(Exception):
    """Base error talking to the bridge."""


class CentsibleConnectionError(CentsibleError):
    """The bridge could not be reached, or answered with something unexpected."""


class CentsibleAuthError(CentsibleError):
    """The device's tokens were rejected: it was revoked, or the token expired."""


class CentsiblePairingError(CentsibleError):
    """The pairing code was wrong, used, or expired."""


class CentsibleRateLimitedError(CentsibleError):
    """Too many failed pairing attempts from this address."""


class CentsibleApiError(CentsibleError):
    """The bridge returned a problem response."""

    def __init__(self, status: int, code: str, detail: str | None) -> None:
        super().__init__(f"{status} {code}: {detail or ''}".strip())
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class Tokens:
    """An access/refresh token pair."""

    access_token: str
    refresh_token: str
    expires_at: datetime

    @classmethod
    def from_response(cls, body: dict[str, Any], now: datetime | None = None) -> Tokens:
        now = now or datetime.now(UTC)
        return cls(
            access_token=body["accessToken"],
            refresh_token=body["refreshToken"],
            expires_at=now + timedelta(seconds=int(body["expiresIn"])),
        )


@dataclass(slots=True)
class PairingInfo:
    """What a pairing link or code tells us."""

    bridge_url: str | None
    code: str
    cf_client_id: str | None = None
    cf_client_secret: str | None = None


@dataclass(slots=True)
class PairResult:
    """A freshly paired device."""

    tokens: Tokens
    device_id: str
    member_name: str
    member_role: str
    budget_ids: list[str]


def parse_pairing_input(value: str) -> PairingInfo:
    """Accept a bare code (``ABCD-EFGH``) or the ``actualbridge://pair?...`` link from the QR code."""
    value = value.strip()
    if value.lower().startswith(f"{PAIRING_SCHEME}://"):
        query = parse_qs(urlparse(value).query)

        def first(key: str) -> str | None:
            return (query.get(key) or [None])[0]

        code = first("c")
        if not code:
            raise ValueError("pairing link has no code")
        return PairingInfo(first("u"), code, first("cfid"), first("cfsecret"))
    return PairingInfo(None, value)


def normalize_url(url: str) -> str:
    """Trim whitespace and trailing slashes; default to https."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("not an http(s) URL")
    return url


class CentsibleClient:
    """Talks to one bridge as one paired device."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        bridge_url: str,
        *,
        tokens: Tokens | None = None,
        cf_client_id: str | None = None,
        cf_client_secret: str | None = None,
        on_tokens: Callable[[Tokens], Awaitable[None] | None] | None = None,
    ) -> None:
        self._session = session
        self._url = normalize_url(bridge_url)
        self._tokens = tokens
        self._cf_id = cf_client_id or None
        self._cf_secret = cf_client_secret or None
        self._on_tokens = on_tokens
        self._lock = asyncio.Lock()

    @property
    def bridge_url(self) -> str:
        return self._url

    @property
    def tokens(self) -> Tokens | None:
        return self._tokens

    # -- auth -----------------------------------------------------------------

    async def pair(self, code: str, device_name: str = DEVICE_NAME) -> PairResult:
        """Redeem a single-use pairing code for this bridge."""
        try:
            body = await self._raw(
                "POST",
                "/v1/auth/pair",
                json={"code": code.strip(), "deviceName": device_name, "platform": "other"},
                auth=False,
            )
        except CentsibleApiError as err:
            if err.status == 429 or err.code == "rate_limited":
                raise CentsibleRateLimitedError(str(err)) from err
            if err.status in (400, 401, 404):
                raise CentsiblePairingError(str(err)) from err
            raise
        tokens = Tokens.from_response(body)
        await self._store(tokens)
        member = body["member"]
        return PairResult(
            tokens=tokens,
            device_id=body["device"]["id"],
            member_name=member["displayName"],
            member_role=member["role"],
            budget_ids=list(member.get("budgetIds") or []),
        )

    async def logout(self) -> None:
        """Revoke this device on the bridge (best effort)."""
        await self.request("POST", "/v1/auth/logout")

    async def _store(self, tokens: Tokens) -> None:
        self._tokens = tokens
        if self._on_tokens is not None:
            result = self._on_tokens(tokens)
            if asyncio.iscoroutine(result):
                await result

    async def _access_token(self, *, force: bool = False, stale: str | None = None) -> str:
        async with self._lock:
            tokens = self._tokens
            if tokens is None:
                raise CentsibleAuthError("not paired")
            # Another caller already refreshed while we waited for the lock.
            if stale is not None and tokens.access_token != stale:
                return tokens.access_token
            fresh = tokens.expires_at - TOKEN_SKEW > datetime.now(UTC)
            if fresh and not force:
                return tokens.access_token
            try:
                body = await self._raw(
                    "POST",
                    "/v1/auth/refresh",
                    json={"refreshToken": tokens.refresh_token},
                    auth=False,
                )
            except CentsibleApiError as err:
                if err.status in (400, 401, 403):
                    raise CentsibleAuthError(str(err)) from err
                raise
            new = Tokens.from_response(body)
            await self._store(new)
            return new.access_token

    # -- transport --------------------------------------------------------------

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        """Authenticated request; refreshes and retries once on 401."""
        token = await self._access_token()
        try:
            return await self._raw(method, path, params=params, json=json, token=token)
        except CentsibleApiError as err:
            if err.status != 401:
                raise
        token = await self._access_token(force=True, stale=token)
        try:
            return await self._raw(method, path, params=params, json=json, token=token)
        except CentsibleApiError as err:
            if err.status == 401:
                raise CentsibleAuthError(str(err)) from err
            raise

    async def _raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        token: str | None = None,
        auth: bool = True,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if self._cf_id and self._cf_secret:
            headers["CF-Access-Client-Id"] = self._cf_id
            headers["CF-Access-Client-Secret"] = self._cf_secret
        if auth and token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with self._session.request(
                method,
                f"{self._url}{path}",
                params=params,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=False,
            ) as resp:
                if resp.status == 204:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                if resp.status >= 300:
                    if "json" in ctype:
                        problem = await resp.json(content_type=None)
                        raise CentsibleApiError(
                            resp.status,
                            str(problem.get("code", "unknown")),
                            problem.get("detail") or problem.get("title"),
                        )
                    # Cloudflare Access answers with a login page or a redirect, not JSON.
                    if resp.status in (301, 302, 303, 307, 308, 401, 403) and "html" in ctype:
                        raise CentsibleConnectionError("The bridge is behind Cloudflare Access: add the service token")
                    raise CentsibleApiError(resp.status, "http_error", await resp.text())
                if "json" not in ctype:
                    raise CentsibleConnectionError(f"Expected JSON from {path}, got {ctype or 'nothing'}")
                return await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CentsibleConnectionError(f"Cannot reach the bridge: {err}") from err

    # -- reads ----------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        return await self._raw("GET", "/v1/health", auth=False)

    async def capabilities(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/capabilities")

    async def budgets(self) -> list[dict[str, Any]]:
        return (await self.request("GET", "/v1/budgets"))["items"]

    async def accounts(self, budget_id: str) -> list[dict[str, Any]]:
        return (await self.request("GET", f"/v1/budgets/{budget_id}/accounts"))["items"]

    async def budget_month(self, budget_id: str, month: str) -> dict[str, Any] | None:
        try:
            return await self.request("GET", f"/v1/budgets/{budget_id}/months/{month}")
        except CentsibleApiError as err:
            if err.status == 404:
                return None
            raise

    async def review_inbox(self, budget_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/v1/budgets/{budget_id}/review", params={"limit": 1})
