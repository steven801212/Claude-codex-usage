"""token-panel: FastAPI app serving /api/usage and the PWA."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .claude import ClaudeProvider
from .codex import CodexProvider
from .common import iso_from_ts, now_ts

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("token-panel")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CLAUDE_CRED = Path(os.environ.get("CLAUDE_CREDENTIALS", DATA_DIR / "claude" / ".credentials.json"))
CODEX_AUTH = Path(os.environ.get("CODEX_AUTH", DATA_DIR / "codex" / "auth.json"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "300"))
STALE_AFTER_SECONDS = int(os.environ.get("STALE_AFTER_SECONDS", str(POLL_SECONDS * 3)))
MANUAL_REFRESH_MIN_GAP = 30
STATIC_DIR = Path(__file__).parent / "static"


class UsageCache:
    """Per-provider cache. A failure keeps the last good data but marks it stale."""

    def __init__(self, providers: list[Any]):
        self.providers = providers
        self.state: dict[str, dict[str, Any]] = {
            p.name: {"data": None, "error": None, "last_success": None, "last_attempt": None}
            for p in providers
        }
        self._refresh_lock = asyncio.Lock()

    async def _one(self, p: Any) -> None:
        st = self.state[p.name]
        st["last_attempt"] = now_ts()
        try:
            st["data"] = await p.fetch()
            st["error"] = None
            st["last_success"] = st["last_attempt"]
        except Exception as e:  # isolate providers from each other
            log.warning("%s fetch failed: %s", p.name, e)
            st["error"] = str(e) or e.__class__.__name__

    async def refresh_all(self) -> None:
        async with self._refresh_lock:
            await asyncio.gather(*(self._one(p) for p in self.providers))

    def snapshot(self) -> dict[str, Any]:
        now = now_ts()
        out: dict[str, Any] = {}
        for name, st in self.state.items():
            data = st["data"] or {}
            last = st["last_success"]
            stale = last is None or st["error"] is not None or (now - last) > STALE_AFTER_SECONDS
            out[name] = {
                "ok": st["error"] is None and last is not None,
                "stale": stale,
                "error": st["error"],
                "last_success": iso_from_ts(last),
                "last_attempt": iso_from_ts(st["last_attempt"]),
                "plan": data.get("plan"),
                "windows": data.get("windows") or {"five_hour": None, "weekly": None},
            }
        return {"server_time": iso_from_ts(now), "poll_seconds": POLL_SECONDS, "providers": out}


async def _poll_loop(cache: UsageCache) -> None:
    while True:
        try:
            await cache.refresh_all()
        except Exception:  # never let the loop die
            log.exception("poll loop error")
        await asyncio.sleep(POLL_SECONDS)


def create_app(providers: list[Any] | None = None, start_poller: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = None
        provs = providers
        if provs is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True)
            provs = [ClaudeProvider(CLAUDE_CRED, client), CodexProvider(CODEX_AUTH, client)]
        app.state.cache = UsageCache(provs)
        app.state.last_manual = 0.0
        task = asyncio.create_task(_poll_loop(app.state.cache)) if start_poller else None
        try:
            yield
        finally:
            if task:
                task.cancel()
            if client:
                await client.aclose()

    app = FastAPI(title="token-panel", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/api/usage")
    async def usage(request: Request):
        return JSONResponse(request.app.state.cache.snapshot(), headers={"Cache-Control": "no-store"})

    @app.post("/api/refresh")
    async def manual_refresh(request: Request):
        st = request.app.state
        if now_ts() - st.last_manual >= MANUAL_REFRESH_MIN_GAP:
            st.last_manual = now_ts()
            await st.cache.refresh_all()
        return JSONResponse(st.cache.snapshot(), headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.middleware("http")
    async def no_cache_shell(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest"):
            resp.headers["Cache-Control"] = "no-cache"
        if request.url.path == "/sw.js":
            resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
