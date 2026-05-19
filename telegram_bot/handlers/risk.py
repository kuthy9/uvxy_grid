# telegram_bot/handlers/risk.py
"""/risk — per-symbol risk_state baseline (prev_close) + 账户级
account_risk_events. 不做任何规则重算.

两张表分布在不同 DB:
  - risk_state           : per-symbol trades_*.db (risk_manager._persist_prev_close)
                           列: timestamp, prev_close, note
  - account_risk_events  : account.db              (account_risk.AccountRiskManager)
                           列: timestamp, event_type, details, action_taken

历史上 handler 错把两条都当 account.db 的 risk_state/risk_events 来查,
account.db 里没有 prev_close 字段, 同时 risk_events 表也不存在 (account.db
实际表叫 account_risk_events). 表现是 /risk 永远返回 'none'.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro


@register("/risk", "per-symbol prev_close baseline + 账户级风控事件 (read-only)")
def handle(args: list, ctx: dict) -> str:
    account_db: Path = Path(ctx["account_db"])
    db_paths: dict[str, Path] = ctx.get("db_paths", {})
    limit = int(ctx.get("event_limit", 5))

    lines: list[str] = []

    # ── per-symbol risk_state baseline (prev_close) ──
    lines.append("[risk_state baseline (per-symbol)]")
    if not db_paths:
        lines.append("none (no per-symbol DBs)")
    else:
        any_row = False
        for sym, p in sorted(db_paths.items()):
            try:
                row = sqlite_ro.query_one(
                    Path(p),
                    "SELECT timestamp, prev_close, note FROM risk_state "
                    "ORDER BY id DESC LIMIT 1",
                )
            except sqlite3.OperationalError as e:
                if "no such table" in str(e).lower():
                    row = None
                else:
                    raise
            if row is None:
                lines.append(f"{sym}: none")
            else:
                any_row = True
                lines.append(
                    f"{sym}: prev_close={row['prev_close']} "
                    f"note={row['note']!r} ts={row['timestamp']}"
                )
        if not any_row:
            # 不再额外追加 — 各 symbol 已经各自输出一行 "none"
            pass

    # ── account 级 risk events ──
    lines.append("")
    lines.append(f"[last {limit} account_risk_events]")
    if not account_db.exists():
        lines.append("none (no account.db)")
        return "\n".join(lines)

    try:
        events = sqlite_ro.query_all(
            account_db,
            "SELECT timestamp, event_type, details, action_taken "
            "FROM account_risk_events ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            events = []
        else:
            raise

    if not events:
        lines.append("none")
    else:
        for e in events:
            lines.append(
                f"{e['timestamp']} {e['event_type']}: "
                f"{e['details']} → {e['action_taken']}"
            )
    return "\n".join(lines)
