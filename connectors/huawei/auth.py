"""Huawei SmartPVMS NBI — OAuth 2.0 authentication client."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
import structlog

from .exceptions import HuaweiAPIError, HuaweiAuthError

logger = structlog.get_logger(__name__)


@dataclass
class _CachedToken:
    access_token: str
    # monotonic timestamp after which the token is considered expired
    expires_at: float


class HuaweiAuthClient:
    """Manages OAuth 2.0 tokens for the Huawei FusionSolar NBI.

    Token lifecycle:
    - Fetched on first call to get_token().
    - Cached in memory only — never written to disk.
    - Proactively refreshed 60 s before expiry.
    - Invalidated and re-fetched on error code 305 (session expired).

    Thread safety: an asyncio.Lock ensures only one coroutine fetches
    a new token at a time, even under concurrent requests.
    """

    AUTH_PATH = "/rest/openapi/pvms/nbi/v1/auth/token"

    def __init__(self, domain: str, client_id: str, client_secret: str) -> None:
        self._domain = domain
        self._client_id = client_id
        self._client_secret = client_secret
        self._cache: _CachedToken | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_token(self) -> str:
        """Return a valid access token, fetching a new one if needed."""
        async with self._lock:
            if self._is_valid():
                return self._cache.access_token  # type: ignore[union-attr]
            return await self._fetch()

    async def refresh_token(self) -> str:
        """Force-invalidate the cached token and fetch a new one."""
        async with self._lock:
            self._cache = None
            return await self._fetch()

    def invalidate(self) -> None:
        """Mark the current token as expired (called on error 305)."""
        self._cache = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_valid(self) -> bool:
        return self._cache is not None and time.monotonic() < self._cache.expires_at - 60

    async def _fetch(self) -> str:
        url = f"https://{self._domain}{self.AUTH_PATH}"
        logger.debug("huawei.auth.fetching_token", domain=self._domain)

        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                resp = await http.post(
                    url,
                    json={"clientId": self._client_id, "clientSecret": self._client_secret},
                    headers={"Content-Type": "application/json"},
                )
        except httpx.RequestError as exc:
            raise HuaweiAuthError(f"Network error during authentication: {exc}") from exc

        if resp.status_code == 401:
            raise HuaweiAuthError("HTTP 401 — invalid client_id or client_secret", fail_code=401)

        if resp.status_code != 200:
            raise HuaweiAuthError(
                f"Unexpected HTTP {resp.status_code} from auth endpoint", fail_code=resp.status_code
            )

        body = resp.json()
        self._raise_for_fail_code(body)

        data = body.get("data") or {}
        token = data.get("accessToken") or data.get("access_token") or ""
        if not token:
            raise HuaweiAuthError(f"No access_token in auth response: {body}")

        expires_in = int(data.get("expiresIn") or data.get("expires_in") or 3600)
        self._cache = _CachedToken(
            access_token=token,
            expires_at=time.monotonic() + expires_in,
        )

        logger.info("huawei.auth.token_obtained", expires_in=expires_in, domain=self._domain)
        return token

    @staticmethod
    def _raise_for_fail_code(body: dict) -> None:
        fail_code = body.get("failCode") or body.get("code")
        if fail_code and int(fail_code) not in (0, 200):
            message = body.get("message") or body.get("msg") or ""
            if int(fail_code) in (305, 401):
                raise HuaweiAuthError(
                    f"Auth failCode {fail_code}: {message}", fail_code=int(fail_code)
                )
            raise HuaweiAPIError.from_fail_code(int(fail_code), detail=message)
