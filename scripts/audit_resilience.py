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
import shutil
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


# ─────────────────────────── Checks ───────────────────────────

EXPECTED_TABLES = [
    "state_machine_state",
    "state_transitions",
    "risk_events",
    "risk_state",
    "trades",
    "daily_snapshots",
    "pnl_fifo_queue",
    "pnl_closes",
]

APPEND_ONLY_EVENT_TABLES = ["state_transitions", "risk_events", "trades"]


def _is_market_hours(now: datetime) -> bool:
    ny = now.astimezone(ZoneInfo("America/New_York"))
    if ny.weekday() >= 5:
        return False
    start = ny.replace(hour=9, minute=30, second=0, microsecond=0)
    end   = ny.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= ny <= end


def check_d_sqlite(db_path: Path, freshness_hours: float,
                   market_hours_only: bool = True) -> CheckResult:
    if not Path(db_path).exists():
        return CheckResult(
            "D", "sqlite", "FAIL",
            observed=f"db file not found: {db_path}",
            expected="SQLite file present + readable + integrity_check ok",
            suggested_action="confirm DB_FILE env / volume mount on Synology",
        )

    uri = f"file:{db_path}?mode=ro&immutable=0"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            present = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            missing = [t for t in EXPECTED_TABLES if t not in present]

            stale: list[str] = []
            now = datetime.now(ZoneInfo("America/New_York"))
            if (not market_hours_only) or _is_market_hours(now):
                for t in APPEND_ONLY_EVENT_TABLES:
                    if t in missing:
                        continue
                    row = conn.execute(
                        f"SELECT MAX(timestamp) FROM {t}"
                    ).fetchone()
                    newest = row[0] if row else None
                    if newest is None:
                        continue
                    try:
                        ts = datetime.fromisoformat(newest)
                    except ValueError:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=ZoneInfo("America/New_York"))
                    delta = now - ts
                    if delta > timedelta(hours=freshness_hours):
                        stale.append(f"{t} newest {ts.isoformat()} ({delta} ago)")
    except sqlite3.DatabaseError as e:
        return CheckResult(
            "D", "sqlite", "FAIL",
            observed=f"DatabaseError: {e}", expected="readable SQLite db",
            suggested_action="check DB file corruption / permissions",
        )

    if integrity != "ok":
        return CheckResult(
            "D", "sqlite", "FAIL",
            observed=f"integrity_check={integrity}",
            expected="integrity_check=ok",
            suggested_action="restore from backup; investigate prior crash",
        )
    if missing:
        return CheckResult(
            "D", "sqlite", "WARN",
            observed=f"missing tables: {missing}",
            expected=f"all of {EXPECTED_TABLES}",
            suggested_action="confirm main bot has run at least once "
                             "so trade_logger/state_machine/etc. have created tables",
        )
    if stale:
        return CheckResult(
            "D", "sqlite", "WARN",
            observed="stale event tables: " + "; ".join(stale),
            expected=f"newest row within {freshness_hours}h during market hours",
            suggested_action="check main bot heartbeat and IBKR connection",
        )

    return CheckResult(
        "D", "sqlite", "OK",
        observed=f"integrity ok, {len(EXPECTED_TABLES)} tables present, "
                 f"event tables fresh",
        expected="—", suggested_action="",
    )


def check_e_json_snapshots(grid_json: Path, base_shares: Path) -> CheckResult:
    issues: list[str] = []
    warns:  list[str] = []
    fails:  list[str] = []

    if not grid_json.exists():
        warns.append(f"grid.json absent at {grid_json} (ok before first ACTIVE_GRID)")
    else:
        try:
            json.loads(grid_json.read_text())
        except json.JSONDecodeError as e:
            fails.append(f"grid.json parse error: {e}")

    if not base_shares.exists():
        warns.append(f"base_shares.txt absent at {base_shares} (ok before any base entry)")
    else:
        try:
            float(base_shares.read_text().strip() or "0")
        except ValueError as e:
            fails.append(f"base_shares.txt not numeric: {e}")

    if fails:
        return CheckResult(
            "E", "json-snapshots", "FAIL",
            observed="; ".join(fails),
            expected="grid.json parseable; base_shares.txt numeric",
            suggested_action="inspect file contents; may indicate mid-write crash",
        )
    if warns:
        return CheckResult(
            "E", "json-snapshots", "WARN",
            observed="; ".join(warns),
            expected="files exist (or expected-absent if bot never reached ACTIVE_GRID)",
            suggested_action="verify against current state machine phase",
        )
    return CheckResult(
        "E", "json-snapshots", "OK",
        observed="grid.json + base_shares.txt present & parseable",
        expected="—", suggested_action="",
    )


def check_b_compose(compose_path: Path) -> CheckResult:
    if not compose_path.exists():
        return CheckResult(
            "B", "compose", "FAIL",
            observed=f"compose file not found: {compose_path}",
            expected="docker-compose.yml with both services",
            suggested_action="run audit from repo root or pass --compose",
        )
    text = compose_path.read_text()

    # Count restart:always occurrences — must be at least 2 (ib-gateway + uvxy-grid).
    restart_always = len(re.findall(r"^\s*restart:\s*always\s*$", text, re.M))
    has_depends_healthy = bool(re.search(
        r"depends_on:\s*\n\s*ib-gateway:\s*\n\s*condition:\s*service_healthy",
        text,
    ))
    healthcheck_retries_match = re.search(r"retries:\s*(\d+)", text)
    retries = int(healthcheck_retries_match.group(1)) if healthcheck_retries_match else 0

    issues = []
    if restart_always < 2:
        issues.append(f"restart:always count={restart_always} (need ≥2)")
    if not has_depends_healthy:
        issues.append("uvxy-grid missing depends_on.condition=service_healthy")
    if retries < 20:
        issues.append(f"healthcheck retries={retries} (recommended ≥20 for 2FA)")

    if issues:
        return CheckResult(
            "B", "compose", "FAIL",
            observed="; ".join(issues),
            expected="restart:always on both services + service_healthy gate + retries≥20",
            suggested_action="restore the gate from spec §4.1 / git history",
        )
    return CheckResult(
        "B", "compose", "OK",
        observed=f"restart_always={restart_always}, healthcheck retries={retries}",
        expected="—", suggested_action="",
    )


def check_j_disk_log(log_path: Path, mount_path: Path,
                     log_max_mb: float, disk_min_gb: float) -> CheckResult:
    issues: list[str] = []
    if not log_path.exists():
        issues.append(f"log file absent: {log_path}")
    else:
        log_mb = log_path.stat().st_size / (1024 * 1024)
        if log_mb > log_max_mb:
            issues.append(f"log size {log_mb:.1f} MB > {log_max_mb} MB")
    try:
        usage = shutil.disk_usage(str(mount_path))
        free_gb = usage.free / (1024 ** 3)
        if free_gb < disk_min_gb:
            issues.append(f"disk free {free_gb:.2f} GB < {disk_min_gb} GB")
    except FileNotFoundError:
        issues.append(f"mount path not found: {mount_path}")

    if issues:
        return CheckResult(
            "J", "disk-log", "WARN",
            observed="; ".join(issues),
            expected=f"log ≤ {log_max_mb} MB, disk ≥ {disk_min_gb} GB free",
            suggested_action="rotate logs / free space on Synology volume",
        )
    return CheckResult(
        "J", "disk-log", "OK",
        observed="log size and disk free within bounds",
        expected="—", suggested_action="",
    )


HEARTBEAT_TABLES = ["state_transitions", "risk_events", "trades", "entry_evaluations"]


def check_k_heartbeat(db_path: Path, freshness_hours: float,
                      market_hours_only: bool = True) -> CheckResult:
    if not Path(db_path).exists():
        return CheckResult(
            "K", "heartbeat", "WARN",
            observed=f"db not found: {db_path}", expected="db file present",
            suggested_action="check DB_FILE / volume mount",
        )
    now = datetime.now(ZoneInfo("America/New_York"))
    in_market = _is_market_hours(now)
    if market_hours_only and not in_market:
        return CheckResult(
            "K", "heartbeat", "OK",
            observed=f"outside market hours ({now.isoformat()}) — skipped",
            expected="—", suggested_action="",
        )

    newest_per_table: dict[str, datetime] = {}
    uri = f"file:{db_path}?mode=ro&immutable=0"
    with sqlite3.connect(uri, uri=True) as conn:
        for t in HEARTBEAT_TABLES:
            try:
                row = conn.execute(f"SELECT MAX(timestamp) FROM {t}").fetchone()
            except sqlite3.OperationalError:
                continue
            if row and row[0]:
                try:
                    ts = datetime.fromisoformat(row[0])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=ZoneInfo("America/New_York"))
                    newest_per_table[t] = ts
                except ValueError:
                    continue

    if not newest_per_table:
        return CheckResult(
            "K", "heartbeat", "WARN",
            observed="no event-bearing table has any rows",
            expected="≥1 row across heartbeat tables",
            suggested_action="confirm main bot has been running",
        )

    most_recent = max(newest_per_table.values())
    delta = now - most_recent
    if delta > timedelta(hours=freshness_hours):
        return CheckResult(
            "K", "heartbeat", "WARN",
            observed=f"stale: most-recent event {most_recent.isoformat()} ({delta} ago, "
                     f"market_hours={in_market})",
            expected=f"within {freshness_hours}h during market hours",
            suggested_action="check main bot process, IBKR connection, and log",
        )
    return CheckResult(
        "K", "heartbeat", "OK",
        observed=f"most-recent event {most_recent.isoformat()} ({delta} ago)",
        expected="—", suggested_action="",
    )


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

    results: list[CheckResult] = []
    results.append(check_d_sqlite(
        db_path=Path(args.db),
        freshness_hours=args.freshness_hours,
        market_hours_only=True,
    ))

    grid_json = Path(args.grid_json) if args.grid_json else Path(str(args.db) + ".grid.json")
    base_shares = Path(args.base_shares) if args.base_shares else Path(str(args.db) + ".base_shares.txt")
    results.append(check_e_json_snapshots(grid_json=grid_json, base_shares=base_shares))

    results.append(check_b_compose(compose_path=Path(args.compose)))

    log_path = Path(args.log)
    results.append(check_j_disk_log(
        log_path=log_path,
        mount_path=log_path.parent if log_path.parent.exists() else Path("."),
        log_max_mb=args.log_max_mb, disk_min_gb=args.disk_min_gb,
    ))

    results.append(check_k_heartbeat(
        db_path=Path(args.db),
        freshness_hours=args.freshness_hours,
        market_hours_only=True,
    ))

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
