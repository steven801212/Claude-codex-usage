import json
from pathlib import Path

from app import claude, codex
from app.common import ts_from_iso

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_claude_parse():
    w = claude.parse_usage(load("claude_usage.json"))
    assert w["five_hour"].used_percent == 37.0
    assert w["five_hour"].resets_at.startswith("2026-11-10T15:00:00")
    assert w["weekly"].used_percent == 82.5
    assert w["weekly"].resets_at == "2026-11-14T03:00:00Z"


def test_claude_parse_nulls():
    w = claude.parse_usage({"five_hour": None, "seven_day": {"utilization": None, "resets_at": None}})
    assert w == {"five_hour": None, "weekly": None}
    w = claude.parse_usage({})
    assert w == {"five_hour": None, "weekly": None}


def test_codex_parse_classifies_by_window_seconds_not_position():
    # primary is the WEEKLY window here, secondary the 5h one.
    out = codex.parse_usage(load("codex_usage.json"), fetched_at=1_762_800_000)
    assert out["plan"] == "plus"
    wk, fh = out["windows"]["weekly"], out["windows"]["five_hour"]
    assert wk.used_percent == 96 and ts_from_iso(wk.resets_at) == 1762900000  # reset_at wins
    assert fh.used_percent == 12 and ts_from_iso(fh.resets_at) == 1_762_800_000 + 3600  # from reset_after_seconds


def test_codex_parse_missing():
    out = codex.parse_usage({"plan_type": "pro", "rate_limit": None})
    assert out == {"plan": "pro", "windows": {"five_hour": None, "weekly": None}}
    out = codex.parse_usage({"rate_limit": {"primary_window": {"used_percent": 5, "limit_window_seconds": 999}}})
    assert out["windows"] == {"five_hour": None, "weekly": None}
