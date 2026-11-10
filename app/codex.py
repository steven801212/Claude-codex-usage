"""ChatGPT / Codex usage via the undocumented wham/usage endpoint."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .common import (
    FIVE_HOUR_SECONDS,
    WEEK_SECONDS,
    AuthError,
    ProviderError,
    Window,
    iso_from_ts,
    now_ts,
    read_json,
    to_float,
    write_json_atomic,
)

log = logging.getLogger("token-panel.codex")

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REFRESH_MARGIN_SECONDS = 5 * 60


def _classify(window_seconds: float | None) -> str | None:
    """Map limit_window_seconds to a slot. Tolerant to small deviations."""
    if window_seconds is None:
        return None
    if abs(window_seconds - FIVE_HOUR_SECONDS) <= 3600:
        return "five_hour"
    if abs(window_seconds - WEEK_SECONDS) <= 86400:
        return "weekly"
    return None


def parse_window(raw: Any, fetched_at: float) -> tuple[str | None, Window] | None:
    if not isinstance(raw, dict):
        return None
    used = to_float(raw.get("used_percent"))
    reset_ts = to_float(raw.get("reset_at"))
    if reset_ts is None:
        after = to_float(raw.get("reset_after_seconds"))
        if after is not None:
            reset_ts = fetched_at + after
    slot = _classify(to_float(raw.get("limit_window_seconds")))
    return slot, Window(used_percent=used, resets_at=iso_from_ts(reset_ts))


def parse_usage(payload: dict[str, Any], fetched_at: float | None = None) -> dict[str, Any]:
    fetched_at = now_ts() if fetched_at is None else fetched_at
    rl = payload.get("rate_limit") or {}
    out: dict[str, Window | None] = {"five_hour": None, "weekly": None}
    for key in ("primary_window", "secondary_window"):
        parsed = parse_window(rl.get(key), fetched_at)
        if not parsed:
            continue
        slot, w = parsed
        if slot and out[slot] is None:
            out[slot] = w
    return {"plan": payload.get("plan_type"), "windows": out}


def _jwt_exp(token: str) -> float | None:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return to_float(json.loads(base64.urlsafe_b64decode(part)).get("exp"))
    except Exception:
        return None


class CodexProvider:
    name = "codex"

    def __init__(self, auth_path: Path, client: httpx.AsyncClient):
        self.auth_path = auth_path
        self.client = client
        self._lock = asyncio.Lock()

    def _load(self) -> tuple[dict[str, Any], dict[str, Any]]:
        data = read_json(self.auth_path)
        tokens = data.get("tokens")
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            raise AuthError("auth.json 內沒有 tokens.access_token，請重新 codex login")
        return data, tokens

    @staticmethod
    def _expiring(tokens: dict[str, Any]) -> bool:
        exp = _jwt_exp(tokens.get("access_token", ""))
        return exp is not None and exp - now_ts() < REFRESH_MARGIN_SECONDS

    async def _refresh(self, stale_access_token: str | None) -> dict[str, Any]:
        async with self._lock:
            data, tokens = self._load()
            if stale_access_token and tokens["access_token"] != stale_access_token and not self._expiring(tokens):
                return tokens
            refresh_token = tokens.get("refresh_token")
            if not refresh_token:
                raise AuthError("沒有 refresh_token，請重新 codex login")
            try:
                r = await self.client.post(
                    TOKEN_URL,
                    json={
                        "client_id": CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "scope": "openid profile email",
                    },
                    headers={"Content-Type": "application/json"},
                )
            except httpx.HTTPError as e:
                raise AuthError(f"Codex token 刷新失敗：{e!r}") from e
            if r.status_code != 200:
                raise AuthError(f"Codex token 刷新失敗：HTTP {r.status_code} {r.text[:200]}")
            tok = r.json()
            if not tok.get("access_token"):
                raise AuthError("Codex token 刷新回應缺 access_token")
            for k in ("access_token", "refresh_token", "id_token"):
                if tok.get(k):
                    tokens[k] = tok[k]
            data["tokens"] = tokens
            data["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            write_json_atomic(self.auth_path, data)
            log.info("Codex token refreshed")
            return tokens

    async def _get(self, tokens: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {tokens['access_token']}",
            "Accept": "application/json",
            "User-Agent": "codex_cli_rs token-panel",
            "originator": "codex_cli_rs",
        }
        if tokens.get("account_id"):
            headers["ChatGPT-Account-Id"] = tokens["account_id"]
        return await self.client.get(USAGE_URL, headers=headers)

    async def fetch(self) -> dict[str, Any]:
        _, tokens = self._load()
        if self._expiring(tokens):
            tokens = await self._refresh(tokens["access_token"])
        r = await self._get(tokens)
        if r.status_code == 401:
            tokens = await self._refresh(tokens["access_token"])
            r = await self._get(tokens)
        if r.status_code != 200:
            raise ProviderError(f"Codex usage HTTP {r.status_code}: {r.text[:200]}")
        parsed = parse_usage(r.json())
        return {
            "plan": parsed["plan"],
            "windows": {k: (v.to_dict() if v else None) for k, v in parsed["windows"].items()},
        }
