# telegram_bot/push/risk_watcher.py
"""Push watcher: new risk_events rows → messages."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from telegram_bot.readers import sqlite_ro


class RiskEventWatcher:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._last_id: int = 0

    def start_baseline(self) -> None:
        self._last_id = sqlite_ro.max_rowid(self.db_path, "risk_events")

    def poll(self) -> list[str]:
        if not self.db_path.exists():
            return []
        try:
            rows = sqlite_ro.query_all(
                self.db_path,
                "SELECT id, timestamp, event_type, details, action_taken "
                "FROM risk_events WHERE id > ? ORDER BY id",
                (self._last_id,),
            )
        except sqlite3.OperationalError as e:
            # risk_events 是 lazily-created 的表 — 主进程的 RiskManager 只在
            # 首次记录风险事件 (DAILY_PNL_LIMIT / SESSION_FREEZE 等) 时才 CREATE.
            # 在那之前 sidecar 每 5s poll 一次会撞 "no such table". start_baseline
            # 已经通过 max_rowid 容忍了这种情形, poll() 此处对齐: 仅吞 missing-table,
            # 其它 OperationalError (corruption / lock) 仍向上抛, 由 _push_loop 兜底.
            if "no such table" in str(e).lower():
                return []
            raise
        if not rows:
            return []
        out: list[str] = []
        for r in rows:
            out.append(
                f"⚠️  risk: {r['event_type']} | {r['details']} "
                f"→ {r['action_taken']} | {r['timestamp']}"
            )
        self._last_id = rows[-1]["id"]
        return out
