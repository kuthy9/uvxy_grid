# telegram_bot/tests/test_push_risk.py
import sqlite3
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
