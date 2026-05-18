# telegram_bot/tests/test_push_state.py
import sqlite3
from telegram_bot.push.state_watcher import StateTransitionWatcher


def test_baseline_then_new_row(make_db):
    db = make_db(rows={
        "state_transitions": [{"timestamp": "t1", "from_state": "A", "to_state": "B", "reason": "x"}],
    })
    w = StateTransitionWatcher(symbol="UVXY", db_path=db)
    w.start_baseline()
    assert w.poll() == []  # no new
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO state_transitions(timestamp, from_state, to_state, reason) "
            "VALUES (?, ?, ?, ?)",
            ("t2", "B", "C", "fill"),
        )
    msgs = w.poll()
    assert len(msgs) == 1
    assert "UVXY" in msgs[0]
    assert "B" in msgs[0] and "C" in msgs[0] and "fill" in msgs[0]
