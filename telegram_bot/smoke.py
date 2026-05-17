# telegram_bot/smoke.py
"""--smoke entry — non-network, non-IBKR dry run.

Runs each reader and each push watcher's start_baseline() against actual
local files. Output is one line per component. Exits 0 if no FAIL, else 1.

Required by spec §3 P2 as the evidence-of-running artifact.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from glob import glob
from pathlib import Path

logger = logging.getLogger("telegram_bot.smoke")


def smoke_against(
    db_glob: str,
    account_db: Path,
    log_path: Path,
    report_dir: Path,
) -> list[dict]:
    """Programmatic entry — used by tests."""
    from telegram_bot.readers import sqlite_ro, grid_json, base_shares, log_tail
    from telegram_bot.push.state_watcher import StateTransitionWatcher
    from telegram_bot.push.risk_watcher import RiskEventWatcher
    from telegram_bot.push.log_watcher import LogPatternWatcher
    from telegram_bot.push.heartbeat import HeartbeatWatcher

    results: list[dict] = []

    # ─── DB discovery ───
    dbs = sorted(glob(db_glob))
    if not dbs:
        results.append({"component": "sqlite_ro", "status": "WARN",
                        "observed": f"no DBs matching {db_glob}"})
    for db_str in dbs:
        db = Path(db_str)
        try:
            row = sqlite_ro.query_one(
                db, "SELECT current_state FROM state_machine_state WHERE id=1"
            )
            tables = sqlite_ro.query_all(
                db, "SELECT name FROM sqlite_master WHERE type='table'"
            )
            results.append({"component": "sqlite_ro", "status": "OK",
                            "observed": f"{db.name} tables={len(tables)} "
                                        f"current_state={row['current_state'] if row else '?'}"})
        except Exception as e:
            results.append({"component": "sqlite_ro", "status": "FAIL",
                            "observed": f"{db.name}: {type(e).__name__}: {e}"})

    # ─── grid_json + base_shares per DB ───
    for db_str in dbs:
        db = Path(db_str)
        gj_path = Path(str(db) + ".grid.json")
        gj_data = grid_json.parse(gj_path)
        if gj_data is None and not gj_path.exists():
            results.append({"component": "grid_json", "status": "WARN",
                            "observed": f"{gj_path.name} absent (ok pre-grid)"})
        elif gj_data is None:
            results.append({"component": "grid_json", "status": "FAIL",
                            "observed": f"{gj_path.name} unparseable"})
        else:
            results.append({"component": "grid_json", "status": "OK",
                            "observed": f"{gj_path.name} levels={len(gj_data.get('levels', {}))}"})

        bs_path = Path(str(db) + ".base_shares.txt")
        bs_val = base_shares.parse(bs_path)
        if bs_val is None and not bs_path.exists():
            results.append({"component": "base_shares", "status": "WARN",
                            "observed": f"{bs_path.name} absent (ok pre-grid)"})
        elif bs_val is None:
            results.append({"component": "base_shares", "status": "FAIL",
                            "observed": f"{bs_path.name} non-numeric"})
        else:
            results.append({"component": "base_shares", "status": "OK",
                            "observed": f"{bs_path.name} value={bs_val}"})

    # ─── log_tail ───
    if log_path.exists():
        lt = log_tail.LogTail(log_path)
        last5 = lt.read_last_n(5)
        results.append({"component": "log_tail", "status": "OK",
                        "observed": f"{log_path.name} size={log_path.stat().st_size} "
                                    f"last_lines={len(last5)}"})
    else:
        results.append({"component": "log_tail", "status": "WARN",
                        "observed": f"{log_path} absent"})

    # ─── account.db / risk watcher baseline ───
    if account_db.exists():
        try:
            rw = RiskEventWatcher(db_path=account_db)
            rw.start_baseline()
            results.append({"component": "risk_watcher", "status": "OK",
                            "observed": f"{account_db.name} baseline={rw._last_id}"})
        except Exception as e:
            results.append({"component": "risk_watcher", "status": "FAIL",
                            "observed": f"{type(e).__name__}: {e}"})
    else:
        results.append({"component": "risk_watcher", "status": "WARN",
                        "observed": f"{account_db} absent"})

    # ─── state watchers ───
    for db_str in dbs:
        db = Path(db_str)
        sym = db.stem.removeprefix("trades_").upper()
        try:
            sw = StateTransitionWatcher(symbol=sym, db_path=db)
            sw.start_baseline()
            results.append({"component": "state_watcher", "status": "OK",
                            "observed": f"{sym} baseline={sw._last_id}"})
        except Exception as e:
            results.append({"component": "state_watcher", "status": "FAIL",
                            "observed": f"{sym}: {type(e).__name__}: {e}"})

    # ─── log watcher ───
    try:
        lw = LogPatternWatcher(log_path=log_path,
                               patterns=[r"\bERROR\b", r"Traceback"],
                               debounce_sec=0)
        lw.start_baseline()
        results.append({"component": "log_watcher", "status": "OK",
                        "observed": f"baseline at EOF of {log_path.name}"})
    except Exception as e:
        results.append({"component": "log_watcher", "status": "FAIL",
                        "observed": f"{type(e).__name__}: {e}"})

    # ─── heartbeat watcher ───
    try:
        db_paths = {Path(d).stem.removeprefix("trades_").upper(): Path(d) for d in dbs}
        hb = HeartbeatWatcher(db_paths=db_paths, stale_min=30,
                              market_hours_only=False)
        msgs = hb.poll(now=datetime.now())
        results.append({"component": "heartbeat", "status": "OK",
                        "observed": f"polled {len(db_paths)} dbs, alerts={len(msgs)}"})
    except Exception as e:
        results.append({"component": "heartbeat", "status": "FAIL",
                        "observed": f"{type(e).__name__}: {e}"})

    # ─── reports dir ───
    if report_dir.exists():
        weekly = sorted(report_dir.glob("weekly_*.html"))
        if weekly:
            results.append({"component": "reports", "status": "OK",
                            "observed": f"newest={weekly[-1].name}"})
        else:
            results.append({"component": "reports", "status": "WARN",
                            "observed": f"{report_dir} has no weekly_*.html yet"})
    else:
        results.append({"component": "reports", "status": "WARN",
                        "observed": f"{report_dir} absent"})

    return results


def run_smoke() -> int:
    """CLI entry called from bot.main(--smoke)."""
    from telegram_bot import config as C
    results = smoke_against(
        db_glob=C.DB_GLOB,
        account_db=Path(C.ACCOUNT_DB_FILE),
        log_path=Path(C.LOG_FILE),
        report_dir=Path(C.REPORT_DIR),
    )
    print(f"{'COMPONENT':<16} {'STATUS':<6} OBSERVED")
    print("-" * 80)
    fails = 0
    for r in results:
        print(f"{r['component']:<16} {r['status']:<6} {r['observed']}")
        if r["status"] == "FAIL":
            fails += 1
    print(f"\ntotal={len(results)} fails={fails}")
    return 0 if fails == 0 else 1
