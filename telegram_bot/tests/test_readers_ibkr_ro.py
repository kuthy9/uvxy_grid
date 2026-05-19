from unittest.mock import MagicMock
import pytest
from telegram_bot.readers import ibkr_ro


def test_write_api_ban_at_import():
    """No public name on IBKRReadOnly may match a write-API regex."""
    forbidden = ibkr_ro.WRITE_API_PATTERN
    for name in dir(ibkr_ro.IBKRReadOnly):
        if name.startswith("_"):
            continue
        assert not forbidden.search(name), (
            f"IBKRReadOnly exposes method {name!r} matching write-API pattern"
        )


def test_portfolio_default_does_not_refresh():
    """默认 refresh=False — 行为完全等同旧实现, 不应触发 reqAccountUpdates."""
    ib = MagicMock()
    ib.portfolio.return_value = [MagicMock()]
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.portfolio() == ib.portfolio.return_value
    ib.portfolio.assert_called_once_with()
    ib.reqAccountUpdates.assert_not_called()
    ib.sleep.assert_not_called()


def test_portfolio_refresh_true_triggers_reqAccountUpdates_and_sleep():
    """refresh=True 时应调 reqAccountUpdates(True) 并 sleep, 再返回 portfolio."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.portfolio.return_value = ["fresh-item"]
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    result = client.portfolio(refresh=True, settle_sec=0.05)
    assert result == ["fresh-item"]
    ib.reqAccountUpdates.assert_called_once_with(True)
    ib.sleep.assert_called_once_with(0.05)
    ib.portfolio.assert_called_once_with()


def test_portfolio_refresh_does_not_call_when_disconnected():
    """断连时跳过 refresh — 不应试图 reqAccountUpdates (会抛 not-connected)."""
    ib = MagicMock()
    ib.isConnected.return_value = False
    ib.portfolio.return_value = []
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    client.portfolio(refresh=True)
    ib.reqAccountUpdates.assert_not_called()
    ib.sleep.assert_not_called()
    # 仍然应当 fall through 到 portfolio() — 返回缓存值 (可能空, 但不抛)
    ib.portfolio.assert_called_once_with()


def test_portfolio_refresh_failure_falls_back_to_cache():
    """reqAccountUpdates 抛异常时, sidecar 必须返回缓存值不抛."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.reqAccountUpdates.side_effect = ConnectionError("transient")
    ib.portfolio.return_value = ["cached-item"]
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    result = client.portfolio(refresh=True)
    # 不抛, 返回缓存
    assert result == ["cached-item"]
    ib.portfolio.assert_called_once_with()


def test_open_orders_calls_ib_req():
    ib = MagicMock()
    ib.reqAllOpenOrders.return_value = ["a", "b"]
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.open_orders() == ["a", "b"]


def test_account_summary_filters_keys():
    ib = MagicMock()
    Item = MagicMock
    items = [Item(tag="NetLiquidation", value="10000"),
             Item(tag="UnrealizedPnL", value="42")]
    ib.accountSummary.return_value = items
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    out = client.account_summary(["NetLiquidation", "UnrealizedPnL"])
    assert out["NetLiquidation"] == "10000"
    assert out["UnrealizedPnL"] == "42"


def test_is_connected_reports_state():
    ib = MagicMock()
    ib.isConnected.return_value = True
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.is_connected() is True


def test_reconnect_if_needed_skips_when_connected():
    ib = MagicMock()
    ib.isConnected.return_value = True
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.reconnect_if_needed(host="x", port=1, client_id=2) is True
    ib.connect.assert_not_called()


def test_reconnect_if_needed_calls_connect_when_disconnected():
    ib = MagicMock()
    ib.isConnected.side_effect = [False, True]  # False before, True after
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.reconnect_if_needed(host="x", port=1, client_id=2) is True
    ib.connect.assert_called_once_with(host="x", port=1, clientId=2, readonly=True)


def test_reconnect_if_needed_returns_false_on_exception():
    ib = MagicMock()
    ib.isConnected.return_value = False
    ib.connect.side_effect = ConnectionRefusedError("nope")
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.reconnect_if_needed(host="x", port=1, client_id=2) is False
