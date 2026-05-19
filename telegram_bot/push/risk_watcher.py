# telegram_bot/push/risk_watcher.py
"""Push watcher: new account_risk_events rows → messages.

数据源: account.db 里的 account_risk_events 表 (由 account_risk.py 写入,
schema 见该模块 _init_db). 历史上这里查的是 risk_events (per-symbol DB 用
的表名), 但 watcher 的 db_path 一直被注入 account_db (见 bot.py:154),
导致永远查不到、push 完全失效. 列定义两边一致, 差的仅是表名前缀.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from telegram_bot.readers import sqlite_ro

_TABLE = "account_risk_events"


class RiskEventWatcher:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._last_id: int = 0

    def start_baseline(self) -> None:
        self._last_id = sqlite_ro.max_rowid(self.db_path, _TABLE)

    def poll(self) -> list[str]:
        if not self.db_path.exists():
            return []
        try:
            rows = sqlite_ro.query_all(
                self.db_path,
                f"SELECT id, timestamp, event_type, details, action_taken "
                f"FROM {_TABLE} WHERE id > ? ORDER BY id",
                (self._last_id,),
            )
        except sqlite3.OperationalError as e:
            # account_risk_events 是 lazily-created 的表 — AccountRiskManager
            # 在 _init_db() 时建出, 但极端早期窗口 (首次启动尚未跑 _init_db)
            # 可能还不存在. start_baseline 已经通过 max_rowid 容忍此情形,
            # poll() 此处对齐: 仅吞 missing-table, 其它 OperationalError
            # (corruption / lock) 仍向上抛, 由 _push_loop 兜底.
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
