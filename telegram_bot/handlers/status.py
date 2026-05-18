# telegram_bot/handlers/status.py
"""/status — per-symbol state + IBKR connection + last-step heartbeat."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro


def _last_step_age(db_path: Path, now: datetime) -> str:
    """Return 'N s ago' / 'N m ago' / 'N h ago' / '—' from newest event."""
    candidates: list[datetime] = []
    for table, col in [
        ("state_transitions", "timestamp"),
        ("trades", "timestamp"),
        ("state_machine_state", "updated_at"),
    ]:
        try:
            row = sqlite_ro.query_one(
                db_path, f"SELECT MAX({col}) AS t FROM {table}"
            )
        except Exception:
            continue
        if row and row.get("t"):
            try:
                candidates.append(datetime.fromisoformat(row["t"]))
            except ValueError:
                continue
    if not candidates:
        return "—"
    delta = (now - max(candidates)).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    return f"{int(delta // 3600)}h ago"


@register("/status", "overall state + IBKR + last-step per sub-bot")
def handle(args: list, ctx: dict) -> str:
    db_paths: dict[str, Path] = ctx["db_paths"]
    ibkr = ctx["ibkr"]
    now: datetime = ctx.get("now") or datetime.now()

    lines: list[str] = []
    ibkr_status = "ok" if ibkr.is_connected() else "DOWN"
    lines.append(f"ibkr={ibkr_status}")
    for sym in sorted(db_paths):
        path = Path(db_paths[sym])
        if not path.exists():
            lines.append(f"{sym}: state=? last_step=? (no db at {path})")
            continue
        row = sqlite_ro.query_one(
            path, "SELECT current_state FROM state_machine_state WHERE id=1"
        )
        state = row["current_state"] if row else "?"
        age = _last_step_age(path, now)
        lines.append(f"{sym}: state={state} last_step={age}")
    return "\n".join(lines)
