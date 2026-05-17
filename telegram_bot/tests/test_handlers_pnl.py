# telegram_bot/tests/test_handlers_pnl.py
from unittest.mock import MagicMock
from telegram_bot.handlers import pnl as PnL


def test_pnl_from_ibkr_account_summary():
    ibkr = MagicMock()
    ibkr.account_summary.return_value = {
        "RealizedPnL": "123.45", "UnrealizedPnL": "-12.34",
    }
    out = PnL.handle(args=[], ctx={"ibkr": ibkr, "db_paths": {}})
    assert "RealizedPnL" in out and "123.45" in out
    assert "UnrealizedPnL" in out


def test_pnl_falls_back_to_sqlite_when_ibkr_missing_fields(make_db):
    db = make_db(rows={
        "trades": [
            {"timestamp": "2026-05-17", "action": "sell", "symbol": "UVXY",
             "quantity": 1, "price": 1, "pnl": 50},
            {"timestamp": "2026-05-17", "action": "sell", "symbol": "UVXY",
             "quantity": 1, "price": 1, "pnl": -10},
        ],
    })
    ibkr = MagicMock(); ibkr.account_summary.return_value = {}
    out = PnL.handle(args=[], ctx={"ibkr": ibkr, "db_paths": {"UVXY": db}})
    assert "fallback" in out.lower() or "sqlite" in out.lower()
    assert "40" in out  # realized sum 50 + -10
