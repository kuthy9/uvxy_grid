# telegram_bot/tests/test_handlers_positions.py
from types import SimpleNamespace
from unittest.mock import MagicMock
from telegram_bot.handlers import positions as P


def _pos(symbol, shares, avg, mkt, mkt_val, unr):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=symbol),
        position=shares, averageCost=avg,
        marketPrice=mkt, marketValue=mkt_val,
        unrealizedPNL=unr,
    )


def test_positions_table():
    ibkr = MagicMock()
    ibkr.portfolio.return_value = [
        _pos("UVXY", 100, 12.5, 12.7, 1270.0, 20.0),
        _pos("VXX",  50, 23.4, 23.0, 1150.0, -20.0),
    ]
    out = P.handle(args=[], ctx={"ibkr": ibkr})
    assert "UVXY" in out and "100" in out and "12.5" in out
    assert "VXX" in out and "-20.0" in out


def test_positions_empty():
    ibkr = MagicMock(); ibkr.portfolio.return_value = []
    out = P.handle(args=[], ctx={"ibkr": ibkr})
    assert "no positions" in out.lower()
