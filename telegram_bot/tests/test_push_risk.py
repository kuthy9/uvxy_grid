# telegram_bot/tests/test_push_risk.py
import sqlite3

import pytest

from telegram_bot.push.risk_watcher import RiskEventWatcher


def test_risk_event_baseline_then_new(make_db):
    db = make_db(filename="account.db", rows={
        "risk_events": [
            {"timestamp": "t1", "event_type": "DAILY_PNL_LIMIT",
             "details": "x", "action_taken": "freeze"},
        ],
    })
    w = RiskEventWatcher(db_path=db)
    w.start_baseline()
    assert w.poll() == []
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO risk_events(timestamp, event_type, details, action_taken) "
            "VALUES (?, ?, ?, ?)",
            ("t2", "SESSION_FREEZE", "y", "no entry"),
        )
    msgs = w.poll()
    assert len(msgs) == 1
    assert "SESSION_FREEZE" in msgs[0] and "no entry" in msgs[0]


def test_risk_event_missing_table_returns_empty(make_db):
    """risk_events 表懒创建; poll() 在表不存在时必须静默返回 []."""
    db = make_db(filename="account.db", rows={})
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE risk_events")
    w = RiskEventWatcher(db_path=db)
    w.start_baseline()
    assert w._last_id == 0
    assert w.poll() == []
    # 第二次 poll() 也必须稳定: 不应该把 _last_id 改坏导致后续异常
    assert w.poll() == []


def test_risk_event_other_operational_error_propagates(monkeypatch, make_db, tmp_path):
    """非 missing-table 的 OperationalError 必须抛出, 不能被吞."""
    db = make_db(filename="account.db", rows={})
    w = RiskEventWatcher(db_path=db)
    w.start_baseline()

    def _boom(*_a, **_kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "telegram_bot.push.risk_watcher.sqlite_ro.query_all", _boom
    )
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        w.poll()
