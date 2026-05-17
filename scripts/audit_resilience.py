"""
audit_resilience.py — read-only resilience audit for equity_grid on Synology.

Spec: docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md §4
Plan: docs/superpowers/plans/2026-05-17-resilience-audit.md

This script NEVER writes to the trading database, NEVER edits any core file,
NEVER touches Telegram. It only reads + greps + reports.

Run:
    python scripts/audit_resilience.py
    python scripts/audit_resilience.py --db ./runtime/trades.db --log ./runtime/grid_trader.log

Exit codes:
    0 — all checks OK
    1 — at least one WARN
    2 — at least one FAIL
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


# ─────────────────────────── Data model ───────────────────────────

@dataclass
class CheckResult:
    code: str
    name: str
    status: str          # "OK" | "WARN" | "FAIL"
    observed: str
    expected: str
    suggested_action: str

    def to_dict(self) -> dict:
        return asdict(self)


def overall_exit_code(results: list[CheckResult]) -> int:
    if any(r.status == "FAIL" for r in results):
        return 2
    if any(r.status == "WARN" for r in results):
        return 1
    return 0


# ─────────────────────────── Reporter ───────────────────────────

def render_table(results: list[CheckResult]) -> None:
    print(f"{'CODE':<5} {'CHECK':<24} {'STATUS':<6} OBSERVED → EXPECTED")
    print("-" * 100)
    for r in results:
        line = f"{r.code:<5} {r.name:<24} {r.status:<6} {r.observed}  →  {r.expected}"
        print(line)
        if r.suggested_action:
            print(f"      action: {r.suggested_action}")


def write_json(results: list[CheckResult], path: Path) -> None:
    payload = {
        "generated_at": datetime.now(ZoneInfo("America/New_York")).isoformat(),
        "overall": {
            "exit_code": overall_exit_code(results),
            "fail_count": sum(1 for r in results if r.status == "FAIL"),
            "warn_count": sum(1 for r in results if r.status == "WARN"),
            "ok_count":   sum(1 for r in results if r.status == "OK"),
        },
        "checks": [r.to_dict() for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


# ─────────────────────────── CLI ───────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resilience audit for equity_grid.")
    parser.add_argument("--db", default=os.environ.get("DB_FILE", "./runtime/trades.db"))
    parser.add_argument("--log", default=os.environ.get("LOG_FILE", "./runtime/grid_trader.log"))
    parser.add_argument("--grid-json", default=None,
                        help="Defaults to <db>.grid.json")
    parser.add_argument("--base-shares", default=None,
                        help="Defaults to <db>.base_shares.txt")
    parser.add_argument("--compose", default="./docker-compose.yml")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--freshness-hours", type=float, default=6.0,
                        help="During market hours, newest event must be within this many hours")
    parser.add_argument("--log-max-mb", type=float, default=500.0)
    parser.add_argument("--disk-min-gb", type=float, default=1.0)
    parser.add_argument("--out", default=None,
                        help="JSON output path; default runtime/audit/<ts>.json")
    args = parser.parse_args(argv)

    # Tasks 2..8 will register check functions here.
    results: list[CheckResult] = []

    render_table(results)
    out = Path(args.out) if args.out else Path("runtime/audit") / (
        datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d-%H%M%S") + ".json"
    )
    write_json(results, out)
    print(f"\nJSON written to {out}")

    code = overall_exit_code(results)
    print(f"exit_code={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
