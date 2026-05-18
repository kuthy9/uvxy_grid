# telegram_bot/tests/test_readers_sqlite.py
import sqlite3
from datetime import datetime
import pytest
from telegram_bot.readers import sqlite_ro


def test_query_one_returns_row(make_db):
    db = make_db(rows={
        "state_machine_state": [{
            "id": 1, "current_state": "ACTIVE_GRID", "updated_at": "2026-05-17T10:00:00",
        }],
    })
    row = sqlite_ro.query_one(
        db, "SELECT current_state, updated_at FROM state_machine_state WHERE id=1"
    )
    assert row is not None
    assert row["current_state"] == "ACTIVE_GRID"


def test_query_all_returns_dict_rows(make_db):
    db = make_db(rows={
        "trades": [
            {"timestamp": "2026-05-17T10:00:00", "action": "buy", "symbol": "UVXY",
             "quantity": 10, "price": 12.5},
            {"timestamp": "2026-05-17T10:01:00", "action": "sell", "symbol": "UVXY",
             "quantity": 10, "price": 12.6},
        ],
    })
    rows = sqlite_ro.query_all(db, "SELECT * FROM trades ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["action"] == "buy"
    assert rows[1]["action"] == "sell"


def test_query_refuses_write(make_db):
    db = make_db()
    with pytest.raises(sqlite3.OperationalError):
        sqlite_ro.query_one(db, "INSERT INTO trades(timestamp, action, symbol, quantity, price) VALUES ('x', 'buy', 'X', 1, 1)")
