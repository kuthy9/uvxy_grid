# telegram_bot/handlers/health.py
"""/health — comprehensive read-only self-check."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro


@register("/health", "comprehensive read-only health check")
def handle(args: list, ctx: dict) -> str:
    db_paths: dict[str, Path] = ctx["db_paths"]
    grid_paths: dict[str, Path] = ctx.get("grid_json_paths", {})
    log_path: Path = Path(ctx["log_path"])
    ibkr = ctx["ibkr"]
    now: datetime = ctx.get("now") or datetime.now()
    freshness_min: int = int(ctx.get("freshness_min", 30))

    lines: list[str] = []

    # IBKR
    lines.append(f"[ibkr] {'OK' if ibkr.is_connected() else 'DOWN'}")

    # SQLite integrity + freshness per sub-bot
    for sym in sorted(db_paths):
        p = Path(db_paths[sym])
        if not p.exists():
            lines.append(f"[db:{sym}] FAIL not found {p}")
            continue
        try:
            uri = f"file:{p}?mode=ro&immutable=0"
            with sqlite3.connect(uri, uri=True) as conn:
                integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
            row = sqlite_ro.query_one(
                p, "SELECT updated_at FROM state_machine_state WHERE id=1"
            )
        except sqlite3.DatabaseError as e:
            lines.append(f"[db:{sym}] FAIL {type(e).__name__}: {e}")
            continue
        if integ != "ok":
            lines.append(f"[db:{sym}] FAIL integrity={integ}")
            continue
        if not row or not row.get("updated_at"):
            lines.append(f"[db:{sym}] WARN no state_machine_state row")
            continue
        ts = datetime.fromisoformat(row["updated_at"])
        delta = (now - ts)
        if delta > timedelta(minutes=freshness_min):
            lines.append(f"[db:{sym}] STALE heartbeat {ts} ({delta} ago)")
        else:
            lines.append(f"[db:{sym}] OK integrity+fresh ({delta} ago)")

    # grid.json existence
    for sym, gp in grid_paths.items():
        if Path(gp).exists():
            lines.append(f"[grid:{sym}] OK file present")
        else:
            lines.append(f"[grid:{sym}] WARN absent (ok pre-grid)")

    # log file
    if log_path.exists():
        size_mb = log_path.stat().st_size / (1024 * 1024)
        lines.append(f"[log] OK {size_mb:.2f} MB")
    else:
        lines.append(f"[log] FAIL not found {log_path}")

    return "\n".join(lines)
