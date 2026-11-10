"""End-to-end with fake HTTP: refresh/rotation, write-back, provider isolation, API shape."""
import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import claude, codex
from app.main import create_app, UsageCache

FIX = Path(__file__).parent / "fixtures"


def jwt(exp):
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b({'alg': 'none'})}.{b({'exp': exp})}.sig"


def write(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))


def make_claude_creds(path, expires_in=3600):
    write(path, {"claudeAiOauth": {"accessToken": "old-at", "refreshToken": "old-rt",
                                   "expiresAt": int((time.time() + expires_in) * 1000),
                                   "subscriptionType": "max"}, "other": 1})


def make_codex_auth(path, access):
    write(path, {"OPENAI_API_KEY": None, "tokens": {"access_token": access, "refresh_token": "c-old-rt",
                                                    "id_token": "id-old", "account_id": "acct-1"}})


def test_claude_401_triggers_refresh_and_rotation(tmp_path):
    cred = tmp_path / "claude" / ".credentials.json"
    make_claude_creds(cred)
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if req.url.host == "api.anthropic.com":
            assert req.headers["anthropic-beta"] == "oauth-2025-04-20"
            if req.headers["authorization"] == "Bearer old-at":
                return httpx.Response(401, json={"error": "expired"})
            assert req.headers["authorization"] == "Bearer new-at"
            return httpx.Response(200, json=json.loads((FIX / "claude_usage.json").read_text()))
        if req.url.host == "platform.claude.com":
            return httpx.Response(500, text="boom")  # fall back to console
        if req.url.host == "console.anthropic.com":
            body = json.loads(req.content)
            assert body == {"grant_type": "refresh_token", "refresh_token": "old-rt",
                            "client_id": claude.CLIENT_ID}
            return httpx.Response(200, json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 28800})
        raise AssertionError(req.url)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await claude.ClaudeProvider(cred, c).fetch()

    out = asyncio.run(run())
    assert out["plan"] == "max"
    assert out["windows"]["weekly"]["used_percent"] == 82.5
    saved = json.loads(cred.read_text())
    assert saved["other"] == 1
    assert saved["claudeAiOauth"]["accessToken"] == "new-at"
    assert saved["claudeAiOauth"]["refreshToken"] == "new-rt"
    assert saved["claudeAiOauth"]["expiresAt"] > time.time() * 1000 + 28000 * 1000
    assert (cred.stat().st_mode & 0o777) == 0o600


def test_claude_proactive_refresh_before_expiry(tmp_path):
    cred = tmp_path / ".credentials.json"
    make_claude_creds(cred, expires_in=60)  # < 5 minutes left
    seen = []

    def handler(req):
        seen.append(req.url.host)
        if req.url.host == "platform.claude.com":
            return httpx.Response(200, json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer new-at"
        return httpx.Response(200, json={"five_hour": {"utilization": 1, "resets_at": None}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await claude.ClaudeProvider(cred, c).fetch()

    asyncio.run(run())
    assert seen == ["platform.claude.com", "api.anthropic.com"]


def test_codex_401_refresh_writes_back(tmp_path):
    auth = tmp_path / "codex" / "auth.json"
    make_codex_auth(auth, "c-old-at")

    def handler(req):
        if req.url.host == "chatgpt.com":
            assert req.headers["chatgpt-account-id"] == "acct-1"
            if req.headers["authorization"] == "Bearer c-old-at":
                return httpx.Response(401)
            return httpx.Response(200, json=json.loads((FIX / "codex_usage.json").read_text()))
        if req.url.host == "auth.openai.com":
            body = json.loads(req.content)
            assert body == {"client_id": codex.CLIENT_ID, "grant_type": "refresh_token",
                            "refresh_token": "c-old-rt", "scope": "openid profile email"}
            return httpx.Response(200, json={"access_token": "c-new-at", "refresh_token": "c-new-rt", "id_token": "id-new"})
        raise AssertionError(req.url)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await codex.CodexProvider(auth, c).fetch()

    out = asyncio.run(run())
    assert out["plan"] == "plus"
    assert out["windows"]["five_hour"]["used_percent"] == 12
    saved = json.loads(auth.read_text())
    assert saved["tokens"] == {"access_token": "c-new-at", "refresh_token": "c-new-rt",
                               "id_token": "id-new", "account_id": "acct-1"}
    assert "last_refresh" in saved


class Fake:
    def __init__(self, name, result=None, exc=None):
        self.name, self.result, self.exc = name, result, exc

    async def fetch(self):
        if self.exc:
            raise self.exc
        return self.result


def test_api_isolation_and_stale():
    good = Fake("claude", {"plan": "max", "windows": {"five_hour": {"used_percent": 10, "resets_at": None}, "weekly": None}})
    bad = Fake("codex", exc=RuntimeError("network down"))
    app = create_app(providers=[good, bad], start_poller=False)
    with TestClient(app) as tc:
        asyncio.run(app.state.cache.refresh_all())
        j = tc.get("/api/usage").json()
        c, x = j["providers"]["claude"], j["providers"]["codex"]
        assert c["ok"] and not c["stale"] and c["error"] is None and c["last_success"]
        assert c["windows"]["five_hour"]["used_percent"] == 10
        assert not x["ok"] and x["stale"] and x["error"] == "network down" and x["last_success"] is None

        # claude later fails: keep last good data, but flag stale + error + last_success
        good.exc = RuntimeError("HTTP 500")
        asyncio.run(app.state.cache.refresh_all())
        c = tc.get("/api/usage").json()["providers"]["claude"]
        assert c["stale"] and c["error"] == "HTTP 500"
        assert c["windows"]["five_hour"]["used_percent"] == 10 and c["last_success"]

        # static PWA files
        assert tc.get("/").status_code == 200
        assert tc.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
        assert tc.get("/sw.js").status_code == 200
        assert tc.get("/icons/icon-192.png").status_code == 200


def test_missing_credentials_is_reported(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as c:
            cache = UsageCache([claude.ClaudeProvider(tmp_path / "nope.json", c),
                                codex.CodexProvider(tmp_path / "nope2.json", c)])
            await cache.refresh_all()
            return cache.snapshot()

    snap = asyncio.run(run())
    assert "請先在容器內登入" in snap["providers"]["claude"]["error"]
    assert "請先在容器內登入" in snap["providers"]["codex"]["error"]
