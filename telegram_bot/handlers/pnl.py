# telegram_bot/handlers/pnl.py
"""/pnl — realized + unrealized; fallback to SQLite trades.pnl sum when IBKR misses fields."""
from __future__ import annotations

from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro

TAGS = ["RealizedPnL", "UnrealizedPnL"]


@register("/pnl", "realized + unrealized PnL (IBKR authoritative, SQLite fallback)")
def handle(args: list, ctx: dict) -> str:
    ibkr = ctx["ibkr"]
    db_paths: dict[str, Path] = ctx.get("db_paths", {})

    summary = ibkr.account_summary(TAGS)
    if summary.get("RealizedPnL") is not None and summary.get("UnrealizedPnL") is not None:
        return "\n".join(f"{k} = {summary[k]}" for k in TAGS)

    # fallback: sum trades.pnl across all per-symbol DBs
    total = 0.0
    counted = 0
    for sym, p in db_paths.items():
        row = sqlite_ro.query_one(p, "SELECT COALESCE(SUM(pnl), 0) AS s FROM trades")
        if row:
            total += float(row["s"])
            counted += 1
    return f"(SQLite fallback, {counted} DBs)\nRealizedPnL sum = {total:.2f}"
