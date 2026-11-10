"""Claude (claude.ai subscription) usage via the undocumented OAuth usage endpoint."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from .common import (
    AuthError,
    ProviderError,
    Window,
    now_ts,
    read_json,
    to_float,
    ts_from_iso,
    iso_from_ts,
    write_json_atomic,
)

log = logging.getLogger("token-panel.claude")

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URLS = (
    "https://platform.claude.com/v1/oauth/token",
    "https://console.anthropic.com/v1/oauth/token",
)
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_MARGIN_SECONDS = 5 * 60


def parse_usage(payload: dict[str, Any]) -> dict[str, Window | None]:
    """Turn the raw /api/oauth/usage response into our window model."""

    def win(key: str) -> Window | None:
        raw = payload.get(key)
        if not isinstance(raw, dict):
            return None
        used = to_float(raw.get("utilization"))
        reset_ts = ts_from_iso(raw.get("resets_at"))
        if used is None and reset_ts is None:
            return None
        return Window(used_percent=used, resets_at=iso_from_ts(reset_ts))

    return {"five_hour": win("five_hour"), "weekly": win("seven_day")}


class ClaudeProvider:
    name = "claude"

    def __init__(self, cred_path: Path, client: httpx.AsyncClient):
        self.cred_path = cred_path
        self.client = client
        self._lock = asyncio.Lock()

    # ---- credentials -------------------------------------------------
    def _load(self) -> tuple[dict[str, Any], dict[str, Any]]:
        data = read_json(self.cred_path)
        oauth = data.get("claudeAiOauth")
        if not isinstance(oauth, dict) or not oauth.get("accessToken"):
            raise AuthError("憑證檔內沒有 claudeAiOauth.accessToken，請重新 /login")
        return data, oauth

    async def _refresh(self, stale_access_token: str | None) -> str:
        async with self._lock:
            data, oauth = self._load()
            # Another caller (or the CLI) may have refreshed already.
            if stale_access_token and oauth["accessToken"] != stale_access_token and not self._expiring(oauth):
                return oauth["accessToken"]
            refresh_token = oauth.get("refreshToken")
            if not refresh_token:
                raise AuthError("沒有 refreshToken，請重新 /login")
            body = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            }
            last_err = "unknown"
            for url in TOKEN_URLS:
                try:
                    r = await self.client.post(url, json=body, headers={"Content-Type": "application/json"})
                except httpx.HTTPError as e:
                    last_err = f"{url}: {e!r}"
                    continue
                if r.status_code != 200:
                    last_err = f"{url}: HTTP {r.status_code} {r.text[:200]}"
                    continue
                tok = r.json()
                access = tok.get("access_token")
                if not access:
                    last_err = f"{url}: 回應缺 access_token"
                    continue
                oauth["accessToken"] = access
                if tok.get("refresh_token"):  # rotated — MUST persist
                    oauth["refreshToken"] = tok["refresh_token"]
                expires_in = to_float(tok.get("expires_in"))
                if expires_in:
                    oauth["expiresAt"] = int((now_ts() + expires_in) * 1000)
                if isinstance(tok.get("scope"), str):
                    oauth["scopes"] = tok["scope"].split()
                data["claudeAiOauth"] = oauth
                write_json_atomic(self.cred_path, data)
                log.info("Claude token refreshed via %s", url)
                return access
            raise AuthError(f"Claude token 刷新失敗：{last_err}")

    @staticmethod
    def _expiring(oauth: dict[str, Any]) -> bool:
        exp_ms = to_float(oauth.get("expiresAt"))
        if exp_ms is None:
            return False
        return exp_ms / 1000 - now_ts() < REFRESH_MARGIN_SECONDS

    # ---- fetch -------------------------------------------------------
    async def _get(self, token: str) -> httpx.Response:
        return await self.client.get(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Accept": "application/json",
                "User-Agent": "claude-code/2.0 token-panel",
            },
        )

    async def fetch(self) -> dict[str, Any]:
        _, oauth = self._load()
        token = oauth["accessToken"]
        if self._expiring(oauth):
            token = await self._refresh(token)
        r = await self._get(token)
        if r.status_code == 401:
            token = await self._refresh(token)
            r = await self._get(token)
        if r.status_code != 200:
            raise ProviderError(f"Claude usage HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        windows = parse_usage(payload)
        _, oauth = self._load()
        return {
            "plan": oauth.get("subscriptionType"),
            "windows": {k: (v.to_dict() if v else None) for k, v in windows.items()},
        }
