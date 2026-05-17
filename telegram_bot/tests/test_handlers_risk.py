# telegram_bot/tests/test_handlers_risk.py
from telegram_bot.handlers import risk as R


def test_risk_shows_prev_close_and_events(make_db):
    db = make_db(filename="account.db", rows={
        "risk_state": [
            {"timestamp": "2026-05-16T16:00:00", "prev_close": 12.10, "note": "snapshot"},
            {"timestamp": "2026-05-17T16:00:00", "prev_close": 12.34, "note": "snapshot"},
        ],
        "risk_events": [
            {"timestamp": "2026-05-17T10:00:00", "event_type": "DAILY_PNL_LIMIT",
             "details": "down 2.1% vs prev_close 12.34", "action_taken": "freeze entries"},
        ],
    })
    out = R.handle(args=[], ctx={
        "account_db": db,
        "event_limit": 5,
    })
    assert "12.34" in out  # latest prev_close
    assert "DAILY_PNL_LIMIT" in out
    assert "freeze entries" in out
