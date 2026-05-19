"""Bug 2 fix: _handle_exit_pending 用本地算术 (actual - filled_*)
推算剩余, 替代 ib.portfolio() 异步缓存重读.

Background: ib_insync 的 portfolio() 是异步事件缓存. 一笔市价 SELL 通过
Trade.isDone() 同步确认成交后, IBKR 的 positionEvent 还没推到本地缓存
就被 _handle_exit_pending 读取, 缓存还停留在 pre-fill 数值 → 误报
"EXIT后仍有 137 股, 下轮重试". 下一轮 sleep(2) 后缓存其实已经刷新,
但日志已经留下假象, 也写了 EXIT_INCOMPLETE 风险事件污染审计.

修复 contract:
  * _liquidate_grid_shares / _liquidate_base_shares 返回 float (实际成交量)
  * _handle_exit_pending 用 actual - filled_grid - filled_base 推算剩余
  * 持仓信息只在循环开头读一次, 不在 fill 后立即重读
"""
import inspect
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

import config
from grid_bot import GridBot
from state_machine import StateMachine, SystemState
from interfaces import HistoricalClock


# ───────────── unit: 修复后的方法签名 ─────────────

def test_liquidate_grid_shares_returns_float():
    """新签名: 必须是 -> float, 才能让上层用本地算术推算 remaining."""
    sig = inspect.signature(GridBot._liquidate_grid_shares)
    assert sig.return_annotation is float, (
        f"_liquidate_grid_shares 必须返回 float, 实际 {sig.return_annotation}"
    )


def test_liquidate_base_shares_returns_float():
    sig = inspect.signature(GridBot._liquidate_base_shares)
    assert sig.return_annotation is float, (
        f"_liquidate_base_shares 必须返回 float, 实际 {sig.return_annotation}"
    )


# ───────────── 行为: stale cache 场景下不应误报 ─────────────

class _StaleCacheExecutor:
    """模拟 ib.portfolio() 异步缓存滞后:

    fill 之后 get_position_details 仍返回 pre-fill 数值, 直到下一轮 tick.
    这就是 Bug 2 的真实生产场景.
    """

    def __init__(self, pre_fill_shares: float, fill_quantity: float,
                 fill_price: float = 35.0):
        self._stale_shares = pre_fill_shares
        self._fill_quantity = fill_quantity
        self._fill_price = fill_price
        self.market_orders = []
        self.cancel_calls = 0

    def cancel_all_orders(self):
        self.cancel_calls += 1

    def sleep(self, _s):
        pass

    def get_position_details(self):
        # 关键: 不论调用多少次, 都返回 stale 数值. 修复后的代码不应该
        # 在 fill 之后再次调用它来判定 "remaining".
        return {
            "shares": self._stale_shares,
            "avg_cost": 30.0,
            "market_value": self._stale_shares * self._fill_price,
            "unrealized_pnl": 0.0,
        }

    def place_market_order(self, action, quantity, order_type_label):
        oid = 1500 + len(self.market_orders)
        self.market_orders.append(
            {"id": oid, "action": action, "qty": quantity,
             "label": order_type_label}
        )
        return oid

    def wait_for_order_fill(self, oid, timeout_sec=60):
        # 全量成交; 实际 quantity 由测试设定 (允许测 partial 等场景)
        order = next(o for o in self.market_orders if o["id"] == oid)
        return {
            "quantity": min(self._fill_quantity, order["qty"]),
            "fill_price": self._fill_price,
            "commission": 0.35,
        }

    def get_current_price(self):
        return self._fill_price


class _ExitPendingFixture(unittest.TestCase):
    """共享 setUp: 建一个能跑 _handle_exit_pending 的最小 GridBot."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

        from trade_logger import TradeDatabase
        from pnl_tracker import PnLTracker
        from risk_manager import RiskManager
        from entry_filter import EntryFilter
        self.db = TradeDatabase(db_path=self.db_path)
        self.clock = HistoricalClock()
        self.clock.set(datetime(2026, 5, 18, 11, 0))  # 周一 11:00 ET 必在交易时段

        self.sm = StateMachine()
        self.sm.context.current_state = SystemState.EXIT_PENDING

        # 占位 — _make_bot_with_executor 里替换 executor
        self._pnl_factory = lambda: PnLTracker(db_path=self.db_path,
                                               clock=self.clock)
        self._risk_factory = lambda: RiskManager(self.db, clock=self.clock)
        self._entry_factory = EntryFilter

    def tearDown(self):
        from grid_bot import _grid_state_path, _base_shares_path
        for p in (_grid_state_path(self.db_path),
                  _base_shares_path(self.db_path),
                  self.tmp.name):
            if os.path.exists(p):
                os.unlink(p)

    def _make_bot(self, executor, fifo_lots=None, base_shares=0.0):
        pnl = self._pnl_factory()
        for lot in (fifo_lots or []):
            pnl.record_buy(**lot)
        bot = GridBot(
            clock=self.clock, executor=executor, db=self.db, pnl=pnl,
            risk=self._risk_factory(),
            state_machine=self.sm,
            entry_filter=self._entry_factory(),
            data_fetcher=None,
        )
        bot._base_position_shares = base_shares
        return bot


class TestHandleExitPendingLocalArithmetic(_ExitPendingFixture):

    def test_full_fill_with_stale_cache_finalizes_exit(self):
        """关键场景: 137 股全成交, 但 ib.portfolio 缓存仍报 137.
        修复后应当 finalize_exit, 不再写 EXIT_INCOMPLETE.
        """
        ex = _StaleCacheExecutor(pre_fill_shares=137.0, fill_quantity=137.0)
        # FIFO 队列空 (LEGACY 持仓场景) → 全部走 base 清仓路径
        bot = self._make_bot(ex, fifo_lots=[], base_shares=137.0)

        bot._handle_exit_pending()

        # 没有 EXIT_INCOMPLETE 风险事件
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            events = [r[0] for r in conn.execute(
                "SELECT event_type FROM risk_events"
            ).fetchall()]
        assert "EXIT_INCOMPLETE" not in events, (
            "stale cache 误报: EXIT_INCOMPLETE 不应在全成交后被写入. events="
            + str(events)
        )

        # 状态机离开 EXIT_PENDING (走 _finalize_exit 路径)
        assert bot.state_machine.state != SystemState.EXIT_PENDING, (
            f"应当离开 EXIT_PENDING, 实际仍为 {bot.state_machine.state.value}"
        )

    def test_partial_fill_keeps_exit_pending_with_correct_remaining(self):
        """只成交一半: 应写 EXIT_INCOMPLETE 并保留 EXIT_PENDING, 剩余值是本地算术 (actual-filled), 不是 stale cache 值."""
        # pre-fill 100, fill 只成交 40 → 真实剩余 60
        ex = _StaleCacheExecutor(pre_fill_shares=100.0, fill_quantity=40.0)
        bot = self._make_bot(ex, fifo_lots=[], base_shares=100.0)

        bot._handle_exit_pending()

        assert bot.state_machine.state == SystemState.EXIT_PENDING

        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT event_type, details FROM risk_events WHERE event_type='EXIT_INCOMPLETE'"
            ).fetchone()
        assert row is not None, "partial fill 应当写 EXIT_INCOMPLETE"
        # details 应反映本地推算的 60 (= 100 - 40), 而不是 stale cache 的 100.
        assert "60" in row[1], (
            f"EXIT_INCOMPLETE details 应包含本地算术结果 60.0, 实际: {row[1]}"
        )
        # 同时验证不是 stale cache 的 100 — 旧实现会写 "剩余100.0000股"
        assert "100" not in row[1].split("60")[0], (
            f"EXIT_INCOMPLETE details 不应是 stale cache 的 100, 实际: {row[1]}"
        )

    def test_grid_in_fifo_routes_through_grid_liquidation(self):
        """FIFO 队列有 lot → grid_to_sell > 0, 网格清仓路径被触发并返回 float."""
        ex = _StaleCacheExecutor(pre_fill_shares=50.0, fill_quantity=50.0)
        bot = self._make_bot(
            ex,
            fifo_lots=[{"quantity": 30, "price": 28.0, "commission": 0.10}],
            base_shares=20.0,
        )
        bot._handle_exit_pending()
        # 两单都成交 (grid 30 + base 20) → 全清
        assert bot.state_machine.state != SystemState.EXIT_PENDING
        labels = [o["label"] for o in ex.market_orders]
        assert "EXIT_GRID" in labels
        assert "EXIT_BASE" in labels


class TestLiquidateReturnsActualFilled(_ExitPendingFixture):
    """_liquidate_* 返回值契约."""

    def test_grid_full_fill_returns_filled_qty(self):
        ex = _StaleCacheExecutor(pre_fill_shares=10.0, fill_quantity=10.0)
        bot = self._make_bot(ex,
                             fifo_lots=[{"quantity": 10, "price": 30,
                                          "commission": 0.10}],
                             base_shares=0.0)
        filled = bot._liquidate_grid_shares(10.0)
        assert filled == 10.0

    def test_grid_zero_qty_returns_zero(self):
        ex = _StaleCacheExecutor(pre_fill_shares=0.0, fill_quantity=0.0)
        bot = self._make_bot(ex, fifo_lots=[], base_shares=0.0)
        # round_quantity(0.0000001) → 0 → 提前返回
        filled = bot._liquidate_grid_shares(0.0000001)
        assert filled == 0.0

    def test_base_order_fail_returns_zero(self):
        """place_market_order 返回 None → 早退, 返回 0.0."""
        class _RefuseExecutor(_StaleCacheExecutor):
            def place_market_order(self, action, quantity, order_type_label):
                return None
        ex = _RefuseExecutor(pre_fill_shares=20.0, fill_quantity=0.0)
        bot = self._make_bot(ex, fifo_lots=[], base_shares=20.0)
        filled = bot._liquidate_base_shares(20.0)
        assert filled == 0.0

    def test_grid_fill_timeout_returns_zero(self):
        class _TimeoutExecutor(_StaleCacheExecutor):
            def wait_for_order_fill(self, oid, timeout_sec=60):
                return None
        ex = _TimeoutExecutor(pre_fill_shares=15.0, fill_quantity=0.0)
        bot = self._make_bot(
            ex,
            fifo_lots=[{"quantity": 15, "price": 30, "commission": 0.10}],
            base_shares=0.0,
        )
        filled = bot._liquidate_grid_shares(15.0)
        assert filled == 0.0


if __name__ == "__main__":
    unittest.main()
