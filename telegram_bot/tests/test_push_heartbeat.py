# telegram_bot/tests/test_push_heartbeat.py
from datetime import datetime, timedelta
from telegram_bot.push.heartbeat import HeartbeatWatcher


def test_heartbeat_fresh_no_alert(make_db):
    db = make_db(rows={
        "state_transitions": [{"timestamp": "2026-05-17T10:00:00",
                                "from_state": "a", "to_state": "b", "reason": ""}],
    })
    w = HeartbeatWatcher(db_paths={"UVXY": db}, stale_min=30,
                        market_hours_only=False)
    msgs = w.poll(now=datetime(2026, 5, 17, 10, 1))
    assert msgs == []


def test_heartbeat_stale_alerts_once_then_silent(make_db):
    db = make_db(rows={
        "state_transitions": [{"timestamp": "2020-01-01T00:00:00",
                                "from_state": "a", "to_state": "b", "reason": ""}],
    })
    w = HeartbeatWatcher(db_paths={"UVXY": db}, stale_min=30,
                        market_hours_only=False)
    msgs = w.poll(now=datetime(2026, 5, 17, 10, 0))
    assert len(msgs) == 1
    assert "UVXY" in msgs[0] and "stale" in msgs[0].lower()
    # second poll within stale state → silent
    msgs2 = w.poll(now=datetime(2026, 5, 17, 10, 1))
    assert msgs2 == []


def test_heartbeat_recovers_resets_alert(make_db):
    db = make_db(rows={
        "state_transitions": [{"timestamp": "2020-01-01T00:00:00",
                                "from_state": "a", "to_state": "b", "reason": ""}],
    })
    w = HeartbeatWatcher(db_paths={"UVXY": db}, stale_min=30,
                        market_hours_only=False)
    msgs = w.poll(now=datetime(2026, 5, 17, 10, 0))
    assert len(msgs) == 1
    # advance: insert fresh row
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO state_transitions(timestamp, from_state, to_state, reason) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-17T09:59:00", "x", "y", ""),
        )
    msgs2 = w.poll(now=datetime(2026, 5, 17, 10, 0))
    # "recovered" message expected
    assert msgs2 and "recover" in msgs2[0].lower()
