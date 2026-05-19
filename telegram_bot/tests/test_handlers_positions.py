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


def test_positions_forces_portfolio_refresh():
    """Bug J 锁定: /positions 必须用 refresh=True 主动刷新 ib_insync 缓存,
    否则长连接下 portfolio() 可能返回数小时前的快照 (NAS 实测 22h stale)."""
    ibkr = MagicMock()
    ibkr.portfolio.return_value = []
    P.handle(args=[], ctx={"ibkr": ibkr})
    # 确保 handler 调用了 portfolio(refresh=True), 不是 portfolio()
    ibkr.portfolio.assert_called_once()
    call_kwargs = ibkr.portfolio.call_args.kwargs
    call_args = ibkr.portfolio.call_args.args
    assert (call_kwargs.get("refresh") is True
            or (len(call_args) >= 1 and call_args[0] is True)), (
        f"/positions handler 必须传 refresh=True; 实际 args={call_args}, "
        f"kwargs={call_kwargs}"
    )
