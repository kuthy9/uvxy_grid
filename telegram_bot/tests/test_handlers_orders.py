# telegram_bot/tests/test_handlers_orders.py
from types import SimpleNamespace
from unittest.mock import MagicMock
from telegram_bot.handlers import orders as O


def _trade(sym, side, qty, lmt, status, tif="GTC"):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=sym),
        order=SimpleNamespace(action=side, totalQuantity=qty, lmtPrice=lmt, tif=tif),
        orderStatus=SimpleNamespace(status=status),
    )


def test_orders_renders():
    ibkr = MagicMock()
    ibkr.open_orders.return_value = [
        _trade("UVXY", "BUY", 10, 12.30, "Submitted"),
        _trade("VXX",  "SELL", 5, 23.45, "Submitted"),
    ]
    out = O.handle(args=[], ctx={"ibkr": ibkr})
    assert "UVXY" in out and "BUY" in out and "12.30" in out
    assert "VXX" in out and "SELL" in out


def test_orders_empty():
    ibkr = MagicMock(); ibkr.open_orders.return_value = []
    out = O.handle(args=[], ctx={"ibkr": ibkr})
    assert "no open orders" in out.lower()
