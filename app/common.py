"""Shared helpers: time handling, atomic JSON file I/O, window model."""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FIVE_HOUR_SECONDS = 5 * 3600  # 18000
WEEK_SECONDS = 7 * 24 * 3600  # 604800


class ProviderError(Exception):
    """A user-presentable failure of one provider."""


class AuthError(ProviderError):
    """Credentials missing / invalid / refresh failed."""


def now_ts() -> float:
    return time.time()


def iso_from_ts(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def ts_from_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        v = value.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Window:
    used_percent: float | None
    resets_at: str | None  # ISO-8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise AuthError(f"找不到憑證檔 {path}，請先在容器內登入") from e
    except json.JSONDecodeError as e:
        raise AuthError(f"憑證檔 {path} 格式錯誤：{e}") from e
    if not isinstance(data, dict):
        raise AuthError(f"憑證檔 {path} 格式錯誤")
    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically (tmp file + rename) and keep it private (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
