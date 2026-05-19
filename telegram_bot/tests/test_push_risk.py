# telegram_bot/tests/test_push_risk.py
import sqlite3

import pytest

from telegram_bot.push.risk_watcher import RiskEventWatcher


def test_risk_event_baseline_then_new(make_db):
    """RiskEventWatcher 必须读 account.db 的 account_risk_events 表
    (由 account_risk.AccountRiskManager 写入), 不是 per-symbol DB 的
    risk_events. Bug G 之前查错表导致 push 完全失效."""
    db = make_db(filename="account.db", rows={
        "account_risk_events": [
            {"timestamp": "t1", "event_type": "DAILY_PNL_LIMIT",
             "details": "x", "action_taken": "freeze"},
        ],
    })
    w = RiskEventWatcher(db_path=db)
    w.start_baseline()
    assert w.poll() == []
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO account_risk_events"
            "(timestamp, event_type, details, action_taken) "
            "VALUES (?, ?, ?, ?)",
            ("t2", "SESSION_FREEZE", "y", "no entry"),
        )
    msgs = w.poll()
    assert len(msgs) == 1
    assert "SESSION_FREEZE" in msgs[0] and "no entry" in msgs[0]


def test_risk_event_missing_table_returns_empty(make_db):
    """account_risk_events 极早期窗口可能不存在; poll() 必须静默返回 [].
    Regression for commit 0d0aa73 — missing-table tolerance must survive
    the table-rename refactor."""
    db = make_db(filename="account.db", rows={})
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE account_risk_events")
    w = RiskEventWatcher(db_path=db)
    w.start_baseline()
    assert w._last_id == 0
    assert w.poll() == []
    # 第二次 poll() 也必须稳定: 不应该把 _last_id 改坏导致后续异常
    assert w.poll() == []


def test_risk_event_ignores_per_symbol_risk_events_table(make_db):
    """关键回归: account-level watcher 不应误读 per-symbol DB 的
    risk_events. Bug G 的根因是把两套不同语义的表混了."""
    db = make_db(filename="account.db", rows={
        # 模拟 conftest 同时建出两套表 (实际 NAS 上 account.db 只有 account_*),
        # 这里要确保 watcher 只看 account_risk_events 不被 risk_events 干扰.
        "risk_events": [
            {"timestamp": "ts", "event_type": "PER_SYMBOL_NOISE",
             "details": "should be ignored", "action_taken": "n/a"},
        ],
    })
    w = RiskEventWatcher(db_path=db)
    w.start_baseline()
    # baseline 应为 0 (account_risk_events 空), 不应是 risk_events 的 max_rowid
    assert w._last_id == 0
    msgs = w.poll()
    assert msgs == [], f"watcher 误读 per-symbol risk_events: {msgs}"


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
