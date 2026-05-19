"""Bug 1 完整修复: IBKRExecutor.get_realized_pnl 必须把 NaN/Inf 视为缺失.

Background: IBKR reqPnL 在账户无任何已平仓对时, pnl.realizedPnL 返回 NaN
(不是 None). 旧实现:
    if pnl.realizedPnL is not None:
        return float(pnl.realizedPnL)
NaN 漏过 is-None 检查, 沿 grid_bot.py:1436 的 log 行污染 stdout +
报告 HTML. 本地 commit c2613d2 把 HTML 兜底放在 report_generator,
但 stdout 那条仍然出 "$+nan". 本测试锁定源头 fix.
"""
from __future__ import annotations

import math
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ibkr_executor import IBKRExecutor


def _make_exec_with_pnl_entries(entries):
    """构造一个 IBKRExecutor 实例, 不走 __init__ (不连 IBKR), 注入 mock ib."""
    ex = IBKRExecutor.__new__(IBKRExecutor)
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.pnl.return_value = entries
    ex.ib = ib
    return ex


def test_nan_realized_pnl_returns_none():
    """NaN 必须返回 None — 这是 production 现象的直接原因."""
    entry = SimpleNamespace(realizedPnL=float("nan"))
    ex = _make_exec_with_pnl_entries([entry])
    assert ex.get_realized_pnl() is None


def test_inf_realized_pnl_returns_none():
    entry = SimpleNamespace(realizedPnL=float("inf"))
    ex = _make_exec_with_pnl_entries([entry])
    assert ex.get_realized_pnl() is None

    entry_neg = SimpleNamespace(realizedPnL=float("-inf"))
    ex = _make_exec_with_pnl_entries([entry_neg])
    assert ex.get_realized_pnl() is None


def test_normal_realized_pnl_returned_as_float():
    """正常路径不应被破坏."""
    entry = SimpleNamespace(realizedPnL=88.99)
    ex = _make_exec_with_pnl_entries([entry])
    assert ex.get_realized_pnl() == 88.99


def test_none_realized_pnl_still_returns_none():
    """旧行为保留: pnl.realizedPnL is None 也是 缺失."""
    entry = SimpleNamespace(realizedPnL=None)
    ex = _make_exec_with_pnl_entries([entry])
    assert ex.get_realized_pnl() is None


def test_nan_entry_skipped_falls_through_to_valid_entry():
    """多条 entry, NaN 后跟有效值 — 应跳过 NaN 返回有效值."""
    entries = [
        SimpleNamespace(realizedPnL=float("nan")),
        SimpleNamespace(realizedPnL=42.5),
    ]
    ex = _make_exec_with_pnl_entries(entries)
    assert ex.get_realized_pnl() == 42.5


def test_all_nan_entries_returns_none():
    entries = [
        SimpleNamespace(realizedPnL=float("nan")),
        SimpleNamespace(realizedPnL=float("nan")),
    ]
    ex = _make_exec_with_pnl_entries(entries)
    assert ex.get_realized_pnl() is None


def test_empty_pnl_list_returns_none():
    ex = _make_exec_with_pnl_entries([])
    # 当首次为空时 ib.sleep + 再 pnl() 重试一次, 仍为空 → None
    ex.ib.sleep = MagicMock()
    assert ex.get_realized_pnl() is None


def test_disconnected_returns_none_without_calling_pnl():
    ex = IBKRExecutor.__new__(IBKRExecutor)
    ib = MagicMock()
    ib.isConnected.return_value = False
    ex.ib = ib
    assert ex.get_realized_pnl() is None
    ib.pnl.assert_not_called()


def test_exception_during_pnl_call_returns_none():
    """ib.pnl 抛异常时 sidecar/main 都不能崩."""
    ex = IBKRExecutor.__new__(IBKRExecutor)
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.pnl.side_effect = RuntimeError("boom")
    ex.ib = ib
    assert ex.get_realized_pnl() is None
