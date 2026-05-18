# telegram_bot/handlers/risk.py
"""/risk — raw fields from risk_state + recent risk_events. No rule re-computation."""
from __future__ import annotations

from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro


@register("/risk", "raw risk_state baseline + recent risk_events")
def handle(args: list, ctx: dict) -> str:
    db: Path = ctx["account_db"]
    limit = int(ctx.get("event_limit", 5))
    if not Path(db).exists():
        return "risk: no account.db"

    state_row = sqlite_ro.query_one(
        db,
        "SELECT timestamp, prev_close, note FROM risk_state ORDER BY id DESC LIMIT 1",
    )
    events = sqlite_ro.query_all(
        db,
        "SELECT timestamp, event_type, details, action_taken "
        "FROM risk_events ORDER BY id DESC LIMIT ?",
        (limit,),
    )

    lines: list[str] = ["[risk_state baseline]"]
    if state_row:
        lines.append(
            f"prev_close={state_row['prev_close']} "
            f"note={state_row['note']!r} "
            f"ts={state_row['timestamp']}"
        )
    else:
        lines.append("none")

    lines.append("")
    lines.append(f"[last {limit} risk_events]")
    if not events:
        lines.append("none")
    else:
        for e in events:
            lines.append(
                f"{e['timestamp']} {e['event_type']}: "
                f"{e['details']} → {e['action_taken']}"
            )
    return "\n".join(lines)
