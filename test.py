"""
test.py — 核心模块单元测试

覆盖模块:
  - state_machine.py  状态转换
  - pnl_tracker.py    FIFO 买卖配对 / 胜率 / 持久化
  - risk_manager.py   6 层风控
  - grid_engine.py    网格构建 / 信号 / 重置 / 退出

运行:  python test.py
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import config
from state_machine import StateMachine, SystemState
from pnl_tracker import PnLTracker
from risk_manager import RiskManager
from grid_engine import DynamicGridEngine, LevelState, GridSide
from trade_logger import TradeDatabase


def setUpModule():
    """测试 fixture: 锁定 TOTAL_CAPITAL=2000 以复现 V49 阈值假设.
    生产代码从不读这个值 — 它只在 test/backtest 中被显式注入."""
    config.TOTAL_CAPITAL = 2000.0


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.sm = StateMachine()

    def test_initial_state_is_scanning(self):
        self.assertEqual(self.sm.state, SystemState.SCANNING)

    def test_scanning_to_waiting_on_entry_pass(self):
        self.sm.on_entry_evaluation(True, "ok", now=datetime.now())
        self.assertEqual(self.sm.state, SystemState.WAITING_ENTRY)

    def test_waiting_back_to_scanning_on_condition_fail(self):
        self.sm.on_entry_evaluation(True, "ok", now=datetime.now())
        self.sm.on_entry_evaluation(False, "ADX高", now=datetime.now())
        self.assertEqual(self.sm.state, SystemState.SCANNING)

    def test_entry_timeout_returns_to_scanning(self):
        t0 = datetime.now()
        self.sm.on_entry_evaluation(True, "ok", now=t0)
        # ENTRY_MAX_WAIT_BARS × 4h 后应超时
        future = t0 + timedelta(hours=(config.ENTRY_MAX_WAIT_BARS + 1) * 4)
        expired = self.sm.check_entry_timeout(current_time=future)
        self.assertTrue(expired)
        self.assertEqual(self.sm.state, SystemState.SCANNING)

    def test_entry_timeout_not_expired(self):
        t0 = datetime.now()
        self.sm.on_entry_evaluation(True, "ok", now=t0)
        soon = t0 + timedelta(hours=4)
        self.assertFalse(self.sm.check_entry_timeout(current_time=soon))
        self.assertEqual(self.sm.state, SystemState.WAITING_ENTRY)

    def test_grid_active_transition_increments_sessions(self):
        t0 = datetime.now()
        self.sm.on_entry_evaluation(True, "ok", now=t0)
        sessions_before = self.sm.context.total_grid_sessions
        self.sm.on_grid_active(now=t0)
        self.assertEqual(self.sm.state, SystemState.ACTIVE_GRID)
        self.assertEqual(self.sm.context.total_grid_sessions, sessions_before + 1)

    def test_exit_signal_only_fires_in_active_grid(self):
        self.sm.on_exit_signal(True, "ADX高", now=datetime.now())
        # 在 SCANNING 下不应有任何变化
        self.assertEqual(self.sm.state, SystemState.SCANNING)

    def test_full_lifecycle(self):
        t0 = datetime.now()
        self.sm.on_entry_evaluation(True, "ok", now=t0)
        self.sm.on_grid_active(now=t0)
        self.sm.on_exit_signal(True, "ADX高", now=t0)
        self.assertEqual(self.sm.state, SystemState.EXIT_PENDING)
        self.assertEqual(self.sm.context.exit_reason, "ADX高")
        self.sm.on_exit_complete(now=t0)
        self.assertEqual(self.sm.state, SystemState.SCANNING)
        self.assertEqual(self.sm.context.exit_reason, "")  # 清理


class TestPnLTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.pnl = PnLTracker(db_path=self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_fifo_full_match_profit(self):
        self.pnl.record_buy(10, 100.0, 0.35, level_index=-1)
        result = self.pnl.record_sell(10, 110.0, 0.35, level_index=1)
        self.assertIsNotNone(result)
        # gross = 10*110 - 10*100 = 100
        self.assertAlmostEqual(result.gross_pnl, 100.0, places=2)
        # net = 100 - 0.35 - 0.35
        self.assertAlmostEqual(result.net_pnl, 99.30, places=2)
        self.assertTrue(result.is_win)
        self.assertEqual(result.unmatched_quantity, 0.0)

    def test_fifo_partial_match_of_first_lot(self):
        self.pnl.record_buy(10, 100.0, 0.35, level_index=-1)
        result = self.pnl.record_sell(4, 110.0, 0.35, level_index=1)
        # matched_cost = 4*100, matched_commission = 0.35*(4/10) = 0.14
        self.assertAlmostEqual(result.matched_cost, 400.0, places=2)
        self.assertAlmostEqual(result.matched_commission, 0.14, places=2)
        # 剩 6 股在队列里
        qs = self.pnl.get_queue_summary()
        self.assertEqual(qs["count"], 1)
        self.assertAlmostEqual(qs["total_qty"], 6.0, places=4)

    def test_fifo_consumes_multiple_lots(self):
        self.pnl.record_buy(5, 100.0, 0.35, level_index=-1)
        self.pnl.record_buy(5, 102.0, 0.35, level_index=-2)
        result = self.pnl.record_sell(8, 110.0, 0.35, level_index=1)
        # 吃掉第1笔 5 股 + 第2笔 3 股
        # matched_cost = 5*100 + 3*102 = 500 + 306 = 806
        self.assertAlmostEqual(result.matched_cost, 806.0, places=2)
        self.assertEqual(result.matched_buy_count, 2)
        # 队列剩第2笔 2 股
        qs = self.pnl.get_queue_summary()
        self.assertAlmostEqual(qs["total_qty"], 2.0, places=4)

    def test_fifo_underflow_is_detected(self):
        self.pnl.record_buy(5, 100.0, 0.35, level_index=-1)
        result = self.pnl.record_sell(10, 110.0, 0.35, level_index=1)
        self.assertGreater(result.unmatched_quantity, 0)
        self.assertAlmostEqual(result.unmatched_quantity, 5.0, places=4)

    def test_invalid_record_buy_zero_qty_ignored(self):
        self.pnl.record_buy(0, 100.0, 0.0)
        self.assertEqual(len(self.pnl.buy_queue), 0)

    def test_force_liquidate_clears_queue(self):
        self.pnl.record_buy(5, 100.0, 0.35, level_index=-1)
        self.pnl.record_buy(3, 102.0, 0.35, level_index=-2)
        result = self.pnl.force_liquidate_queue(90.0, 0.7, reason="test")
        self.assertIsNotNone(result)
        self.assertEqual(len(self.pnl.buy_queue), 0)
        # 都是亏损价卖出 → net_pnl 应为负
        self.assertLess(result.net_pnl, 0)

    def test_persist_and_reload_queue(self):
        self.pnl.record_buy(5, 100.0, 0.35, level_index=-1)
        self.pnl.record_buy(3, 102.0, 0.35, level_index=-2)
        self.pnl.save_state()
        # 新 tracker 从同一 db 恢复
        other = PnLTracker(db_path=self.tmp.name)
        other.load_state()
        self.assertEqual(len(other.buy_queue), 2)
        qs = other.get_queue_summary()
        self.assertAlmostEqual(qs["total_qty"], 8.0, places=4)

    def test_win_rate_excludes_emergency_liquidation(self):
        self.pnl.record_buy(5, 100.0, 0.35, level_index=-1)
        self.pnl.record_sell(5, 110.0, 0.35, level_index=1)  # 普通平仓 (win)
        self.pnl.record_buy(5, 100.0, 0.35, level_index=-2)
        self.pnl.force_liquidate_queue(80.0, 0.35, reason="hard_stop")  # 不计入
        stats = self.pnl.get_win_rate()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["wins"], 1)


class TestRiskManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = TradeDatabase(db_path=self.tmp.name)
        self.risk = RiskManager(self.db)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_hard_stop_triggers_at_threshold(self):
        equity = config.TOTAL_CAPITAL * (1 - config.HARD_STOP_LOSS_PCT - 0.01)
        result = self.risk.check_hard_stop(equity)
        self.assertFalse(result.allowed)
        self.assertTrue(self.risk.is_hard_stopped())

    def test_hard_stop_does_not_trigger_above_threshold(self):
        equity = config.TOTAL_CAPITAL * (1 - config.HARD_STOP_LOSS_PCT + 0.01)
        result = self.risk.check_hard_stop(equity)
        self.assertTrue(result.allowed)

    def test_hard_stop_latches(self):
        equity = config.TOTAL_CAPITAL * (1 - config.HARD_STOP_LOSS_PCT - 0.01)
        self.risk.check_hard_stop(equity)
        # 恢复资金, 仍应拒绝
        result = self.risk.check_hard_stop(config.TOTAL_CAPITAL * 1.1)
        self.assertFalse(result.allowed)

    def test_position_limit(self):
        limit = config.TOTAL_CAPITAL * config.MAX_POSITION_VALUE_PCT
        self.assertTrue(self.risk.check_position_limit(limit * 0.5).allowed)
        self.assertFalse(self.risk.check_position_limit(limit + 1).allowed)

    def test_flash_crash_layer1_relative_to_prev_close(self):
        self.risk.set_prev_close(100.0)
        # 跌 9% 应触发
        result = self.risk.check_flash_crash(91.0)
        self.assertFalse(result.allowed)

    def test_flash_crash_layer1_not_triggered_small_drop(self):
        self.risk.set_prev_close(100.0)
        result = self.risk.check_flash_crash(95.0)  # 只跌 5%
        # 还要看层2 没有 intraday 快照, 层2 不触发
        self.assertTrue(result.allowed)

    def test_flash_crash_intraday_layer2(self):
        self.risk.set_prev_close(100.0)
        # 喂入滚动最高价 100
        self.risk.record_intraday_price(100.0)
        self.risk.record_intraday_price(99.0)
        # 当前价 94 = 从 100 跌 6%, 超过 5% 默认阈值
        result = self.risk.check_flash_crash(94.0)
        self.assertFalse(result.allowed)

    def test_flash_crash_atr_dynamic_threshold(self):
        self.risk.set_prev_close(100.0)
        self.risk.record_intraday_price(100.0)
        # ATR 3% → 动态阈值 = min(-5%, -9%) = -9% (更严)
        self.risk.update_atr_context(0.03)
        # 只跌 5% 不再触发 (因为需要跌 9%+)
        result = self.risk.check_flash_crash(95.0)
        self.assertTrue(result.allowed)

    def test_flash_crash_latches(self):
        self.risk.set_prev_close(100.0)
        self.risk.check_flash_crash(90.0)  # 触发
        result = self.risk.check_flash_crash(100.0)  # 即使价格恢复
        self.assertFalse(result.allowed)
        self.risk.manual_resume()
        result = self.risk.check_flash_crash(100.0)
        self.assertTrue(result.allowed)

    def test_earnings_freeze_with_no_dates(self):
        # config.EARNINGS_DATES 默认空, 不应冻结
        self.assertTrue(self.risk.check_earnings_freeze().allowed)


class TestGridEngine(unittest.TestCase):
    def setUp(self):
        # grid_capital 足够大, 保证整数股模式下每档 quantity >= 1
        self.engine = DynamicGridEngine(
            center_price=100.0, atr=2.0,
            grid_capital=5000.0,
            current_time=datetime(2025, 1, 1, 10, 0),
        )

    def test_initial_grid_has_correct_level_count(self):
        above = sum(1 for lv in self.engine.levels.values() if lv.side == GridSide.ABOVE)
        below = sum(1 for lv in self.engine.levels.values() if lv.side == GridSide.BELOW)
        self.assertEqual(above, config.GRID_LEVELS)
        self.assertEqual(below, config.GRID_LEVELS)

    def test_grid_levels_are_symmetric(self):
        # 档位1 上下价差应对称
        above = self.engine.levels[1].price
        below = self.engine.levels[-1].price
        self.assertAlmostEqual(above - 100.0, 100.0 - below, places=2)

    def test_buy_signal_triggers_below_center(self):
        target = self.engine.levels[-1].price  # 最近买入档
        signals = self.engine.check_signals(target - 0.01)
        buy_signals = [s for s in signals if s["action"] == "BUY"]
        self.assertGreaterEqual(len(buy_signals), 1)

    def test_sell_signal_triggers_above_center(self):
        target = self.engine.levels[1].price
        signals = self.engine.check_signals(target + 0.01)
        sell_signals = [s for s in signals if s["action"] == "SELL"]
        self.assertGreaterEqual(len(sell_signals), 1)

    def test_filled_level_does_not_emit_signal(self):
        self.engine.mark_order_filled(-1, self.engine.levels[-1].price, "t")
        signals = self.engine.check_signals(self.engine.levels[-1].price - 0.01)
        bought_again = any(s["level_index"] == -1 for s in signals)
        self.assertFalse(bought_again)

    def test_filled_resets_when_price_crosses_center(self):
        self.engine.mark_order_filled(-1, self.engine.levels[-1].price, "t")
        self.assertEqual(self.engine.levels[-1].state, LevelState.FILLED)
        # 价格回到中轴之上 → 买档应重置为 IDLE
        self.engine.check_filled_resets(101.0)
        self.assertEqual(self.engine.levels[-1].state, LevelState.IDLE)

    def test_recenter_requires_min_bars(self):
        t_init = datetime(2025, 1, 1, 10, 0)
        # 刚建完, 距 last_recenter 仅 1 小时 (< GRID_RECENTER_MIN_BARS)
        ok, _ = self.engine.should_recenter(
            current_price=110.0, current_ema=112.0, current_atr=2.0,
            current_time=t_init + timedelta(hours=1),
        )
        self.assertFalse(ok)

    def test_recenter_triggers_when_drift_exceeds_threshold(self):
        t_init = datetime(2025, 1, 1, 10, 0)
        # 4 bars 后 EMA 偏离中轴 > 1 × ATR
        strategy_hours = config.strategy_interval_hours()
        future = t_init + timedelta(hours=strategy_hours * (config.GRID_RECENTER_MIN_BARS + 1))
        ok, reason = self.engine.should_recenter(
            current_price=105.0,
            current_ema=103.0,  # 偏离 3 = 1.5×ATR(2)
            current_atr=2.0,
            current_time=future,
        )
        self.assertTrue(ok)

    def test_recenter_rebuilds_grid(self):
        old_center = self.engine.center_price
        self.engine.recenter(new_center=120.0, new_atr=2.5)
        self.assertEqual(self.engine.center_price, 120.0)
        # 档位价格都应重建
        self.assertNotAlmostEqual(self.engine.levels[1].price,
                                  old_center * (1 + self.engine.spacing_pct))

    def test_exit_adx_trigger(self):
        should, reason = self.engine.should_exit(
            current_price=100.0,
            current_adx=config.EXIT_MAX_ADX + 1,
            current_atr_pct=0.02,
        )
        self.assertTrue(should)
        self.assertIn("ADX", reason)

    def test_exit_atr_explosion_trigger(self):
        should, reason = self.engine.should_exit(
            current_price=100.0,
            current_adx=10.0,
            current_atr_pct=config.EXIT_MAX_ATR_PCT + 0.01,
        )
        self.assertTrue(should)
        self.assertIn("ATR", reason)

    def test_exit_price_deviation_trigger(self):
        # 价格偏离中轴 5×ATR > 阈值 4
        should, reason = self.engine.should_exit(
            current_price=100.0 + 5 * 2.0,
            current_adx=10.0,
            current_atr_pct=0.02,
        )
        self.assertTrue(should)

    def test_exit_not_triggered_in_normal_range(self):
        should, _ = self.engine.should_exit(
            current_price=101.0,
            current_adx=10.0,
            current_atr_pct=0.02,
        )
        self.assertFalse(should)

    def test_spacing_clamped_to_min(self):
        e = DynamicGridEngine(center_price=100.0, atr=0.001, grid_capital=5000.0)
        self.assertGreaterEqual(e.spacing_pct, config.GRID_MIN_SPACING_PCT)

    def test_spacing_clamped_to_max(self):
        e = DynamicGridEngine(center_price=100.0, atr=100.0, grid_capital=5000.0)
        self.assertLessEqual(e.spacing_pct, config.GRID_MAX_SPACING_PCT)

    def test_frozen_grid_emits_no_signals(self):
        self.engine.freeze("test")
        signals = self.engine.check_signals(self.engine.levels[-1].price - 1)
        self.assertEqual(signals, [])


class TestStateMachinePersistence(unittest.TestCase):
    """StateMachine 落盘 & 恢复 (Bug B6 — 重启状态不丢失)"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        # 初始化一个空表 (load/save 会自建)
        self.db_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_save_then_load_preserves_state(self):
        sm = StateMachine()
        t0 = datetime(2026, 4, 22, 10, 30)
        sm.on_entry_evaluation(True, "ok", now=t0)
        sm.on_grid_active(now=t0)
        sm.on_recenter(now=t0)
        sm.save_state(self.db_path)

        # 新实例从同一数据库恢复
        sm2 = StateMachine()
        ok = sm2.load_state(self.db_path)
        self.assertTrue(ok)
        self.assertEqual(sm2.state, SystemState.ACTIVE_GRID)
        self.assertEqual(sm2.context.total_grid_sessions, 1)
        self.assertEqual(sm2.context.total_recenters, 1)

    def test_load_without_prior_save_returns_false(self):
        sm = StateMachine()
        ok = sm.load_state(self.db_path)
        self.assertFalse(ok)
        self.assertEqual(sm.state, SystemState.SCANNING)


class TestGridEnginePersistence(unittest.TestCase):
    """GridEngine JSON 落盘 & 恢复"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_save_load_roundtrip_preserves_levels(self):
        eng = DynamicGridEngine(
            center_price=25.0, atr=0.8, grid_capital=4000.0,
            current_time=datetime(2026, 4, 22, 10, 30),
        )
        eng.mark_order_placed(-1, order_id=42)
        eng.mark_order_filled(1, fill_price=26.0, fill_time="2026-04-22T10:30")
        eng.save_state(self.path)

        restored = DynamicGridEngine.load_state(self.path)
        self.assertAlmostEqual(restored.center_price, 25.0)
        self.assertEqual(restored.total_filled_sells, 1)
        self.assertEqual(restored.levels[-1].state.value, "order_pending")
        self.assertEqual(restored.levels[-1].order_id, 42)


class TestConfigDynamicInterval(unittest.TestCase):
    """config.STRATEGY_INTERVAL 驱动所有周期派生值 (1h/4h/1d)"""

    def tearDown(self):
        config.STRATEGY_INTERVAL = "4h"  # 恢复默认

    def test_hours_and_seconds_derive_from_interval(self):
        config.STRATEGY_INTERVAL = "15m"
        self.assertAlmostEqual(config.strategy_interval_hours(), 0.25)
        self.assertEqual(config.strategy_interval_minutes(), 15)
        self.assertEqual(config.strategy_interval_seconds(), 900)
        self.assertEqual(config.scanning_interval_sec(), 900)

        config.STRATEGY_INTERVAL = "1h"
        self.assertEqual(config.strategy_interval_hours(), 1)
        self.assertEqual(config.strategy_interval_seconds(), 3600)
        self.assertEqual(config.scanning_interval_sec(), 3600)

        config.STRATEGY_INTERVAL = "4h"
        self.assertEqual(config.strategy_interval_hours(), 4)
        self.assertEqual(config.strategy_interval_seconds(), 14400)

        config.STRATEGY_INTERVAL = "1d"
        self.assertEqual(config.strategy_interval_hours(), 24)
        self.assertEqual(config.strategy_interval_seconds(), 86400)

    def test_unsupported_interval_raises(self):
        config.STRATEGY_INTERVAL = "2h"
        with self.assertRaises(ValueError):
            config.strategy_interval_hours()

    def test_waiting_interval_decoupled_from_strategy_interval(self):
        """WAITING_ENTRY 节拍必须与 STRATEGY_INTERVAL 解耦.

        历史 bug: 两者绑定后, STRATEGY_INTERVAL=4h 会让 WAITING_ENTRY 也 4h
        才评估一次, 入场信号到 → 系统睡 4h → 醒来 timing 已脱离 band → 超时.
        现在 waiting_interval_sec() 必须固定返回 WAITING_ENTRY_CHECK_INTERVAL_SEC.
        """
        # 默认值 (5min)
        self.assertEqual(config.WAITING_ENTRY_CHECK_INTERVAL_SEC, 300)
        for iv in ("15m", "1h", "4h", "1d"):
            config.STRATEGY_INTERVAL = iv
            self.assertEqual(config.waiting_interval_sec(),
                             config.WAITING_ENTRY_CHECK_INTERVAL_SEC,
                             f"interval={iv} 时 waiting_interval_sec 不应跟随 STRATEGY_INTERVAL")
            # 与 scanning_interval 严格不同 (1d 例外: 86400 vs 300, 仍然不同)
            self.assertNotEqual(config.waiting_interval_sec(),
                                config.scanning_interval_sec(),
                                f"interval={iv} 时 waiting/scanning 不应相同")

    def test_state_machine_check_interval_uses_waiting_value(self):
        """get_check_interval_sec 在 WAITING_ENTRY 状态下必须返回 5min 级别值,
        而不是 strategy_interval_seconds. 这是 main.py sleep() 的入口."""
        from state_machine import StateMachine, SystemState
        from datetime import datetime as _dt
        clock = MagicMock()
        clock.now.return_value = _dt(2026, 5, 5, 10, 0)
        sm = StateMachine(clock=clock)
        sm.transition_to(SystemState.WAITING_ENTRY, "test", now=clock.now())
        config.STRATEGY_INTERVAL = "4h"
        self.assertEqual(sm.get_check_interval_sec(), config.WAITING_ENTRY_CHECK_INTERVAL_SEC)
        self.assertNotEqual(sm.get_check_interval_sec(), config.strategy_interval_seconds())


class TestIbkrPortLabel(unittest.TestCase):
    def test_known_ports(self):
        self.assertEqual(config.ibkr_port_label(7497)[0], "paper")
        self.assertEqual(config.ibkr_port_label(7496)[0], "live")
        self.assertEqual(config.ibkr_port_label(4002)[0], "paper")
        self.assertEqual(config.ibkr_port_label(4001)[0], "live")

    def test_unknown_port(self):
        mode, desc = config.ibkr_port_label(9999)
        self.assertEqual(mode, "unknown")
        self.assertIn("9999", desc)


class TestSimulatedExecutorRealisticFill(unittest.TestCase):
    """真实化 SimulatedExecutor: 滑点与概率"""

    def _make_df(self):
        # 单一 bar, 价格 10-11, Close 10.5
        import pandas as pd
        idx = pd.DatetimeIndex([datetime(2026, 4, 22, 10, 0)])
        return pd.DataFrame({
            "Open":[10.5],"High":[11.0],"Low":[10.0],"Close":[10.5],"Volume":[100000]
        }, index=idx)

    def test_market_buy_applies_slippage(self):
        from simulated_executor import SimulatedExecutor
        from interfaces import HistoricalClock
        df = self._make_df()
        clk = HistoricalClock(); clk.set(df.index[0])
        ex = SimulatedExecutor(df, 10000.0, clk, realistic=True, seed=1)
        ex.set_bar_index(0)
        oid = ex.place_market_order("BUY", 10)
        fill = ex.wait_for_order_fill(oid)
        self.assertIsNotNone(fill)
        # 市价买: 至少不低于 mid (10.5), 应向上滑点
        self.assertGreaterEqual(fill["fill_price"], 10.5)

    def test_sell_commission_includes_regulatory_fees(self):
        from simulated_executor import _calc_commission
        # 卖 100 股 @ $20 → 名义 $2000
        # SEC: 2000*27.8e-6 = 0.0556; TAF: 100*0.000166=0.0166 (在 [0.01, 8.30] 内, 不截断)
        # IBKR: max(0.35, 100*0.0035=0.35) = 0.35
        # 卖总: 0.35 + 0.0556 + 0.0166 = 0.4222
        fee_sell = _calc_commission(100, 20.0, "SELL")
        fee_buy = _calc_commission(100, 20.0, "BUY")
        self.assertGreater(fee_sell, fee_buy)
        self.assertAlmostEqual(fee_buy, 0.35, places=2)
        self.assertAlmostEqual(fee_sell, 0.35 + 0.0556 + 0.0166, places=3)


class _FakeExecutor:
    """
    精简 Executor mock, 用于测试 grid_bot 恢复/对账/cancel 等流程.
    实现了 GridBot 在 start() + reconcile + persist 路径上用到的全部方法,
    行为参数化便于不同测试场景注入.
    """
    def __init__(self,
                 position_shares: float = 0.0,
                 open_orders: list = None,
                 realized_pnl: float = 0.0,
                 account_summary: dict = None,
                 cancel_fail_ids: set = None,
                 current_price: float = 25.0):
        self._position = position_shares
        self._open_orders = list(open_orders or [])
        self._realized = realized_pnl
        self._summary = account_summary if account_summary is not None else {
            "NetLiquidation": 2000.0,
            "TotalCashValue": 1500.0,
            "GrossPositionValue": 500.0,
            "UnrealizedPnL": 0.0,
            "RealizedPnL": 0.0,
            "BuyingPower": 3000.0,
        }
        self._cancel_fail = set(cancel_fail_ids or [])
        self._price = current_price
        self.adopted: dict[int, dict] = {}
        self.cancelled: list[int] = []
        self.cancel_attempts: list[int] = []
        self._connected = True

    def connect(self): return True
    def disconnect(self): self._connected = False
    def is_connected(self): return self._connected
    def reconcile_on_startup(self):
        return {
            "position_shares": self._position,
            "open_orders": list(self._open_orders),
        }
    def adopt_order(self, order_id, trade, action, quantity, price,
                    level_index=0, order_type="ADOPTED"):
        self.adopted[order_id] = dict(action=action, quantity=quantity,
                                       price=price, level_index=level_index,
                                       order_type=order_type)
    def cancel_order(self, order_id):
        self.cancel_attempts.append(order_id)
        if order_id in self._cancel_fail:
            return False
        self.cancelled.append(order_id)
        return True
    def cancel_all_orders(self):
        for o in list(self.adopted):
            self.cancel_order(o)
    def get_position_details(self):
        return {"shares": self._position, "avg_cost": 20.0,
                "market_value": self._position * self._price,
                "unrealized_pnl": 0.0}
    def get_account_summary(self): return dict(self._summary)
    def get_cash(self): return self._summary.get("TotalCashValue", 0.0)
    def get_current_price(self): return self._price
    def get_prev_close(self): return self._price  # 测试里简化成和当前价相同
    def get_realized_pnl(self): return self._realized


class TestReconcileScenarios(unittest.TestCase):
    """补充 7 个场景测试 (用户关切)"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        from trade_logger import TradeDatabase
        self.db = TradeDatabase(db_path=self.db_path)

    def tearDown(self):
        os.unlink(self.tmp.name)
        from grid_bot import _grid_state_path, _base_shares_path
        for p in (_grid_state_path(self.db_path), _base_shares_path(self.db_path)):
            if os.path.exists(p):
                os.unlink(p)

    def _make_bot(self, executor, grid=None, fifo_lots=None, state=None,
                  base_shares=0.0):
        from interfaces import HistoricalClock
        from grid_bot import GridBot
        from pnl_tracker import PnLTracker
        from risk_manager import RiskManager
        from entry_filter import EntryFilter

        clock = HistoricalClock(); clock.set(datetime(2026, 4, 22, 11, 0))
        pnl = PnLTracker(db_path=self.db_path, clock=clock)
        for lot in (fifo_lots or []):
            pnl.record_buy(**lot)
        pnl.save_state()

        sm = StateMachine()
        if state is not None:
            sm.context.current_state = state
            sm.save_state(self.db_path)

        from grid_bot import _grid_state_path, _base_shares_path
        if grid is not None:
            grid.save_state(_grid_state_path(self.db_path))
        if base_shares > 0:
            with open(_base_shares_path(self.db_path), "w") as f:
                f.write(str(base_shares))

        bot = GridBot(
            clock=clock, executor=executor, db=self.db, pnl=pnl,
            risk=RiskManager(self.db, clock=clock),
            state_machine=StateMachine(),
            entry_filter=EntryFilter(),
            data_fetcher=None,
        )
        # 手动跑 start() 里的恢复 + reconcile, 避开需要 data_fetcher 的路径
        bot.pnl.load_state()
        bot.state_machine.load_state(self.db_path)
        bot._try_restore_grid()
        bot._restore_base_shares()
        bot._reconcile_with_broker()
        return bot

    # ── 场景 1: 本地 grid 快照有, broker 部分订单缺失 ──
    def test_scenario_1_local_grid_but_broker_missing_some_orders(self):
        # 本地 grid 有 3 个挂单 (-1, -2, +1); broker 只返回 101 和 103 (102 丢失).
        # ACTIVE_GRID 下 broker 必有底仓持仓, 否则 reconcile 会判漂移.
        g = DynamicGridEngine(center_price=25.0, atr=0.5, grid_capital=800.0,
                              current_time=datetime(2026, 4, 22, 11, 0))
        g.mark_order_placed(-1, 101)
        g.mark_order_placed(-2, 102)
        g.mark_order_placed(1,  103)
        broker_orders = [
            {"order_id": 101, "action": "BUY",
             "quantity": g.levels[-1].quantity, "price": g.levels[-1].price,
             "trade": object(), "order_type": "GRID_BUY"},
            {"order_id": 103, "action": "SELL",
             "quantity": g.levels[1].quantity,  "price": g.levels[1].price,
             "trade": object(), "order_type": "GRID_SELL"},
        ]
        # broker 仍持有底仓 30 股 (匹配 base_shares)
        ex = _FakeExecutor(position_shares=30.0, open_orders=broker_orders)
        bot = self._make_bot(ex, grid=g, state=SystemState.ACTIVE_GRID,
                             base_shares=30.0)
        # 101 + 103 被接管, 102 不提供→不接管也不误撤
        self.assertIn(101, ex.adopted)
        self.assertIn(103, ex.adopted)
        self.assertNotIn(102, ex.adopted)
        self.assertEqual(ex.cancelled, [])  # broker 没给 102, 我们也没撤别的

    # ── 场景 2: partial fill 后重启 ──
    def test_scenario_2_partial_fill_before_restart(self):
        # 场景: 崩溃前下了 BUY 10 @ $22 的单, 5 股先成交 (broker 记 5 股持仓),
        # 剩 5 股 GTC 仍挂; 本地 FIFO 未更新 (save_state 赶不上).
        g = DynamicGridEngine(center_price=25.0, atr=0.5, grid_capital=800.0,
                              current_time=datetime(2026, 4, 22, 11, 0))
        g.mark_order_placed(-1, 101)
        # Broker: 真实持仓 5 股, 订单仍 open (remaining 5)
        broker_orders = [{
            "order_id": 101, "action": "BUY",
            "quantity": 5, "price": g.levels[-1].price,  # remaining qty
            "trade": object(), "order_type": "GRID_BUY",
        }]
        ex = _FakeExecutor(position_shares=5.0, open_orders=broker_orders)
        # 本地 FIFO 空; base=0; state=ACTIVE_GRID
        bot = self._make_bot(ex, grid=g, state=SystemState.ACTIVE_GRID,
                             fifo_lots=[])
        # broker 持仓 5 vs local expected 0 → QTY_MISMATCH 应有
        with sqlite3.connect(self.db_path) as c:
            events = c.execute(
                "SELECT event_type FROM risk_events"
            ).fetchall()
        types = [e[0] for e in events]
        self.assertIn("RECONCILE_QTY_MISMATCH", types)
        # 订单 101 被接管
        self.assertIn(101, ex.adopted)

    # ── 场景 3: broker 有仓位, 本地 FIFO 丢失 ──
    def test_scenario_3_broker_position_but_fifo_lost(self):
        # state=SCANNING (本地认为空仓), broker 却有 50 股
        ex = _FakeExecutor(position_shares=50.0, open_orders=[])
        bot = self._make_bot(ex, grid=None, state=SystemState.SCANNING)
        # 期望: 进 EXIT_PENDING 等人工介入
        self.assertEqual(bot.state_machine.state, SystemState.EXIT_PENDING)
        with sqlite3.connect(self.db_path) as c:
            types = [r[0] for r in c.execute(
                "SELECT event_type FROM risk_events"
            ).fetchall()]
        self.assertIn("RECONCILE_DRIFT", types)

    # ── 场景 4: orphan order 撤销失败 ──
    def test_scenario_4_orphan_cancel_fail(self):
        # 无本地 grid, broker 有 1 个订单; cancel 会失败
        broker_orders = [{
            "order_id": 999, "action": "BUY", "quantity": 5,
            "price": 20.0, "trade": object(), "order_type": "GRID_BUY",
        }]
        ex = _FakeExecutor(position_shares=0.0, open_orders=broker_orders,
                           cancel_fail_ids={999})
        bot = self._make_bot(ex, grid=None, state=SystemState.SCANNING)
        # 应尝试过 cancel, 但失败不崩溃
        self.assertIn(999, ex.cancel_attempts)
        self.assertNotIn(999, ex.cancelled)
        # 不应 adopt (因为没有 grid 可匹配)
        self.assertNotIn(999, ex.adopted)
        # state 维持 (broker 空仓, state SCANNING 无漂移)
        self.assertEqual(bot.state_machine.state, SystemState.SCANNING)

    # ── 场景 5: IBKR 空/stale 返回, weekly report 能降级 ──
    def test_scenario_5_ibkr_empty_summary_and_stale_pnl(self):
        # account_summary={}, realized=None
        ex = _FakeExecutor(position_shares=0.0,
                           account_summary={},  # 空
                           realized_pnl=None)
        # 直接调用 grid_bot._generate_weekly_report 的 try/except 应静默降级
        from grid_bot import GridBot
        from interfaces import HistoricalClock
        from pnl_tracker import PnLTracker
        from risk_manager import RiskManager
        from entry_filter import EntryFilter
        clock = HistoricalClock(); clock.set(datetime(2026, 4, 22, 11, 0))
        bot = GridBot(
            clock=clock, executor=ex, db=self.db,
            pnl=PnLTracker(db_path=self.db_path, clock=clock),
            risk=RiskManager(self.db, clock=clock),
            state_machine=StateMachine(), entry_filter=EntryFilter(),
            data_fetcher=None,
        )
        # 不应 raise
        bot._generate_weekly_report()  # 内部捕获所有异常

    # ── 场景 6: 风控触发与 reconcile 并发 ──
    def test_scenario_6_hard_stop_during_reconcile(self):
        # 重启前硬止损已触发 (account 跌破 HARD_STOP 阈值).
        # Reconcile 时: broker 有残留仓位, state=ACTIVE_GRID.
        # 期望: reconcile 完成, 然后 _handle_active_grid 触发 emergency_liquidate.
        g = DynamicGridEngine(center_price=25.0, atr=0.5, grid_capital=800.0,
                              current_time=datetime(2026, 4, 22, 11, 0))
        crashed_equity = config.TOTAL_CAPITAL * (1 - config.HARD_STOP_LOSS_PCT - 0.01)
        ex = _FakeExecutor(
            position_shares=50.0,
            open_orders=[],
            account_summary={
                "NetLiquidation": crashed_equity,
                "TotalCashValue": 0, "GrossPositionValue": crashed_equity,
                "UnrealizedPnL": -100, "RealizedPnL": 0, "BuyingPower": 0,
            })
        bot = self._make_bot(ex, grid=g, state=SystemState.ACTIVE_GRID,
                             fifo_lots=[{"quantity":30,"price":20,"commission":0.35}],
                             base_shares=20.0)
        # 触发 risk 检查 + hard stop
        risk_check = bot.risk.can_trade(ex.get_current_price(),
                                        crashed_equity, 500.0)
        self.assertFalse(risk_check.allowed)
        self.assertTrue(bot.risk.is_hard_stopped())

    # ── 场景 7: 周一 16:30 ET 周报跨时区边界 ──
    def test_scenario_7_weekly_report_timezone_boundary(self):
        """
        _maybe_generate_weekly_report 的时区逻辑:
          HistoricalClock: naive 时间直接当 ET
          LiveClock: 用 datetime.now(ET).replace(tzinfo=None)
        本测试: 周一 16:30 ET (恰好触发) vs 周日 23:59 ET (不触发)
        """
        from grid_bot import GridBot
        from interfaces import HistoricalClock
        from pnl_tracker import PnLTracker
        from risk_manager import RiskManager
        from entry_filter import EntryFilter

        clock = HistoricalClock()
        bot = GridBot(
            clock=clock, executor=_FakeExecutor(), db=self.db,
            pnl=PnLTracker(db_path=self.db_path, clock=clock),
            risk=RiskManager(self.db, clock=clock),
            state_machine=StateMachine(), entry_filter=EntryFilter(),
            data_fetcher=None,
        )

        # (a) 周日 23:59 ET → 不该触发
        sunday = datetime(2026, 4, 26, 23, 59)  # 2026-04-26 是周日
        self.assertEqual(sunday.weekday(), 6)  # Sunday
        clock.set(sunday)
        bot._maybe_generate_weekly_report()
        self.assertIsNone(bot._last_weekly_report_week)

        # (b) 周一 16:30 ET → 触发
        monday_1630 = datetime(2026, 4, 27, 16, 30)  # 周一
        self.assertEqual(monday_1630.weekday(), 0)
        clock.set(monday_1630)
        bot._maybe_generate_weekly_report()
        self.assertIsNotNone(bot._last_weekly_report_week)

        # (c) 同一周再触发应幂等 (不重复)
        week_tag = bot._last_weekly_report_week
        monday_later = datetime(2026, 4, 27, 17, 0)
        clock.set(monday_later)
        bot._maybe_generate_weekly_report()
        self.assertEqual(bot._last_weekly_report_week, week_tag)

        # (d) 周一 16:29 (差 1 分钟) → 不触发
        bot._last_weekly_report_week = None
        clock.set(datetime(2026, 4, 27, 16, 29))
        bot._maybe_generate_weekly_report()
        self.assertIsNone(bot._last_weekly_report_week)


class TestRiskManagerClock(unittest.TestCase):
    """v2.3: clock 注入 — backtest 不再用 wall-clock"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = TradeDatabase(db_path=self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_intraday_snapshot_uses_clock_not_wallclock(self):
        from interfaces import HistoricalClock
        clk = HistoricalClock()
        historical_time = datetime(2020, 1, 15, 10, 30)  # 远早于 wall-clock
        clk.set(historical_time)
        risk = RiskManager(self.db, clock=clk)
        risk.record_intraday_price(25.0)
        self.assertEqual(len(risk._intraday_snapshots), 1)
        ts, price = risk._intraday_snapshots[0]
        # 关键: 时间戳必须来自 clock, 不是 wall clock
        self.assertEqual(ts, historical_time)

    def test_check_earnings_freeze_uses_clock_date(self):
        from interfaces import HistoricalClock
        from unittest.mock import patch
        clk = HistoricalClock()
        clk.set(datetime(2026, 6, 15, 10, 30))
        risk = RiskManager(self.db, clock=clk)
        with patch.object(config, "EARNINGS_DATES", ["2026-06-17"]):
            r = risk.check_earnings_freeze()
            # 6-15 在 6-17 ± window 内 → 冻结
            self.assertFalse(r.allowed)

    def test_check_trading_hours_historical_clock_is_et(self):
        from interfaces import HistoricalClock
        clk = HistoricalClock()
        # 周三 12:00 ET (交易时段内)
        clk.set(datetime(2026, 4, 22, 12, 0))
        risk = RiskManager(self.db, clock=clk)
        self.assertTrue(risk.check_trading_hours().allowed)
        # 周三 09:00 ET (未到)
        clk.set(datetime(2026, 4, 22, 9, 0))
        self.assertFalse(risk.check_trading_hours().allowed)
        # 周六 12:00 ET
        clk.set(datetime(2026, 4, 25, 12, 0))
        self.assertFalse(risk.check_trading_hours().allowed)


class TestSimulatedExecutorPrevClose(unittest.TestCase):
    """SimulatedExecutor.get_prev_close(): 前一根 bar 的 Close, 起点 bar 返回 None"""

    def _make_df(self):
        import pandas as pd
        idx = pd.DatetimeIndex([
            datetime(2026, 4, 20, 10, 0),
            datetime(2026, 4, 21, 10, 0),
            datetime(2026, 4, 22, 10, 0),
        ])
        return pd.DataFrame({
            "Open":  [10.0, 11.0, 12.0],
            "High":  [10.5, 11.5, 12.5],
            "Low":   [ 9.5, 10.5, 11.5],
            "Close": [10.2, 11.2, 12.2],
            "Volume":[1000, 1000, 1000],
        }, index=idx)

    def test_prev_close_at_first_bar_is_none(self):
        from simulated_executor import SimulatedExecutor
        from interfaces import HistoricalClock
        df = self._make_df()
        clk = HistoricalClock(); clk.set(df.index[0])
        ex = SimulatedExecutor(df, 10000.0, clk, realistic=False, seed=1)
        ex.set_bar_index(0)
        self.assertIsNone(ex.get_prev_close())

    def test_prev_close_at_bar_k_returns_bar_k_minus_1_close(self):
        from simulated_executor import SimulatedExecutor
        from interfaces import HistoricalClock
        df = self._make_df()
        clk = HistoricalClock(); clk.set(df.index[2])
        ex = SimulatedExecutor(df, 10000.0, clk, realistic=False, seed=1)
        ex.set_bar_index(2)
        self.assertAlmostEqual(ex.get_prev_close(), 11.2, places=6)


class TestRiskInitializeFromDbPriority(unittest.TestCase):
    """initialize_from_db 五档优先级: risk_state > broker > daily_snapshot > current > None"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = TradeDatabase(db_path=self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _risk(self):
        return RiskManager(self.db)

    def test_priority_1_risk_state_wins_over_broker(self):
        r = self._risk()
        r.set_prev_close(111.0)  # 写入 risk_state
        r2 = self._risk()
        r2.initialize_from_db(current_price=300.0, broker_prev_close=222.0)
        self.assertAlmostEqual(r2._prev_close, 111.0, places=6)

    def test_priority_2_broker_when_no_risk_state(self):
        r = self._risk()
        r.initialize_from_db(current_price=300.0, broker_prev_close=222.0)
        self.assertAlmostEqual(r._prev_close, 222.0, places=6)

    def test_priority_3_daily_snapshot_when_no_risk_state_no_broker(self):
        # 塞一条 daily_snapshots (用底层 sqlite)
        with sqlite3.connect(self.tmp.name) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS daily_snapshots (
                date TEXT PRIMARY KEY, grid_center REAL)""")
            conn.execute("INSERT INTO daily_snapshots (date, grid_center) VALUES (?, ?)",
                         ("2026-04-22", 150.0))
        r = self._risk()
        r.initialize_from_db(current_price=300.0, broker_prev_close=None)
        self.assertAlmostEqual(r._prev_close, 150.0, places=6)

    def test_priority_4_current_price_last_resort(self):
        r = self._risk()
        r.initialize_from_db(current_price=300.0, broker_prev_close=None)
        self.assertAlmostEqual(r._prev_close, 300.0, places=6)

    def test_priority_5_all_none_leaves_prev_close_none(self):
        r = self._risk()
        r.initialize_from_db(current_price=None, broker_prev_close=None)
        self.assertIsNone(r._prev_close)

    def test_stale_risk_state_falls_through_to_broker(self):
        """risk_state 快照 > 24h 视为过期, 应落到 broker_prev_close."""
        # 手动写一条 25h 前的快照
        r0 = self._risk()
        r0._ensure_state_table()
        old_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        with sqlite3.connect(self.tmp.name) as conn:
            conn.execute(
                "INSERT INTO risk_state (timestamp, prev_close, note) VALUES (?, ?, ?)",
                (old_ts, 999.0, "stale")
            )
        r = self._risk()
        r.initialize_from_db(current_price=300.0, broker_prev_close=222.0)
        self.assertAlmostEqual(r._prev_close, 222.0, places=6)


class TestIBKRExecutorPrevCloseLogic(unittest.TestCase):
    """
    单元测试 IBKRExecutor.get_prev_close 的"过滤今天"过滤逻辑.
    不启动真的 IBKR — 用 MagicMock 替 self.ib, 直接验证返回选择.
    """

    def _make_executor(self):
        from ibkr_executor import IBKRExecutor
        ex = IBKRExecutor()
        ex.ib = MagicMock()
        ex.ib.isConnected.return_value = True
        return ex

    def _bar(self, d, close):
        b = MagicMock()
        b.date = d
        b.close = close
        return b

    def test_returns_last_completed_bar_when_today_bar_exists(self):
        """市场进行中: 历史 API 可能返回今天的未完成 bar — 必须跳过."""
        from datetime import date as date_cls
        ex = self._make_executor()
        today = date_cls.today()  # 用真实今天, 保证 today_et 匹配
        ex.ib.reqHistoricalData.return_value = [
            self._bar(today - timedelta(days=2), 100.0),
            self._bar(today - timedelta(days=1), 200.0),  # ← 期望值
            self._bar(today,                     300.0),  # ← 必须跳过
        ]
        self.assertAlmostEqual(ex.get_prev_close(), 200.0, places=6)

    def test_returns_last_bar_when_no_today_bar(self):
        """市场收盘: 最新 bar 本身就是昨天已完成 bar — 直接返回."""
        from datetime import date as date_cls
        ex = self._make_executor()
        today = date_cls.today()
        ex.ib.reqHistoricalData.return_value = [
            self._bar(today - timedelta(days=3), 100.0),
            self._bar(today - timedelta(days=2), 150.0),
            self._bar(today - timedelta(days=1), 200.0),
        ]
        self.assertAlmostEqual(ex.get_prev_close(), 200.0, places=6)

    def test_returns_none_when_all_bars_are_today_or_future(self):
        """退化边界: 没有任何 date<today 的 bar."""
        from datetime import date as date_cls
        ex = self._make_executor()
        today = date_cls.today()
        ex.ib.reqHistoricalData.return_value = [
            self._bar(today, 300.0),
        ]
        self.assertIsNone(ex.get_prev_close())

    def test_returns_none_on_empty_bars(self):
        ex = self._make_executor()
        ex.ib.reqHistoricalData.return_value = []
        self.assertIsNone(ex.get_prev_close())

    def test_returns_none_when_disconnected(self):
        ex = self._make_executor()
        ex.ib.isConnected.return_value = False
        self.assertIsNone(ex.get_prev_close())


class TestIBKRExecutorDelayedDegrade(unittest.TestCase):
    """
    验证 live (type=1) 取价超时后自动降级 delayed (type=3):
      - 有显式 WARN 日志
      - 状态缓存到 _market_data_type_effective=3
      - 后续调用不再重试 live
    """

    def _make_executor(self):
        from ibkr_executor import IBKRExecutor
        ex = IBKRExecutor()
        ex.ib = MagicMock()
        ex.ib.isConnected.return_value = True
        ex._market_data_type_effective = 1  # 假装已 connect() 过
        return ex

    def test_live_timeout_triggers_delayed_fallback_and_caches(self):
        import ibkr_executor
        ex = self._make_executor()

        # _poll_price_once 两次调用: 1) live 超时 返 None  2) delayed 返 12.34
        call_count = {"n": 0}
        def poll_stub(timeout_sec):
            call_count["n"] += 1
            return None if call_count["n"] == 1 else 12.34
        ex._poll_price_once = poll_stub

        # 捕获是否真的调了 reqMarketDataType(3)
        apply_log = []
        orig_apply = ex._apply_market_data_type
        def spy_apply(mdt, reason=""):
            apply_log.append(mdt)
            orig_apply(mdt, reason)
        ex._apply_market_data_type = spy_apply

        with self.assertLogs(ibkr_executor.logger, level="WARNING") as cm:
            px = ex.get_current_price()
        self.assertAlmostEqual(px, 12.34, places=4)
        # 降级已触发并留痕
        self.assertEqual(apply_log, [3])
        self.assertEqual(ex._market_data_type_effective, 3)
        self.assertTrue(any("降级为 delayed" in m for m in cm.output))

    def test_second_call_after_degrade_does_not_reset_to_live(self):
        ex = self._make_executor()
        ex._market_data_type_effective = 3  # 之前已降级
        ex._poll_price_once = lambda t: 9.99
        apply_log = []
        ex._apply_market_data_type = lambda m, reason="": apply_log.append(m)
        px = ex.get_current_price()
        self.assertAlmostEqual(px, 9.99, places=4)
        self.assertEqual(apply_log, [])  # 已是 delayed, 不应再调 reqMarketDataType

    def test_reconnect_resets_effective_to_none(self):
        ex = self._make_executor()
        ex._market_data_type_effective = 3
        # 让 connect() 失败, 避免真的连 IBKR, 只验证重置语义
        ex.disconnect = lambda: None
        ex.connect = lambda: False
        import time as _t
        orig_sleep = _t.sleep
        _t.sleep = lambda s: None
        try:
            ex.reconnect()
        finally:
            _t.sleep = orig_sleep
        self.assertIsNone(ex._market_data_type_effective)


class TestIBKRExecutorReconnectRetry(unittest.TestCase):
    """
    实盘 get_current_price 在 Socket disconnect / reqMktData 异常 / 未连接 时,
    必须先 reconnect 再 retry, 不能直接返回 None — 否则 _execute_entry 会因
    取价失败而错过入场. 单次轮询失败 ≠ 整体失败.
    """

    def _make_executor(self):
        from ibkr_executor import IBKRExecutor
        ex = IBKRExecutor()
        ex.ib = MagicMock()
        ex.ib.isConnected.return_value = True
        # 已是 delayed, 避开降级路径让测试聚焦 retry 行为
        ex._market_data_type_effective = 3
        return ex

    def test_first_poll_fails_then_second_succeeds(self):
        """第一次 poll 抛 Socket disconnect, 第二次成功 — 应返回成功价并调过 reconnect."""
        ex = self._make_executor()
        calls = {"n": 0}

        def poll_stub(timeout_sec):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("Socket disconnect")
            return 9.99
        ex._poll_price_once = poll_stub

        reconnect_calls = {"n": 0}
        def reconnect_stub():
            reconnect_calls["n"] += 1
            ex._market_data_type_effective = 3   # 保持 mdt=3 避开降级分支
            return True
        ex.reconnect = reconnect_stub

        original = config.PRICE_RETRY_COUNT
        config.PRICE_RETRY_COUNT = 2
        try:
            px = ex.get_current_price()
        finally:
            config.PRICE_RETRY_COUNT = original

        self.assertAlmostEqual(px, 9.99, places=4)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(reconnect_calls["n"], 1)

    def test_all_polls_fail_returns_none(self):
        """所有 attempt 全部抛 Socket disconnect — 应返回 None, reconnect 被调用过."""
        ex = self._make_executor()
        ex._poll_price_once = MagicMock(
            side_effect=ConnectionError("Socket disconnect")
        )
        ex.reconnect = MagicMock(return_value=True)

        original = config.PRICE_RETRY_COUNT
        config.PRICE_RETRY_COUNT = 2
        try:
            px = ex.get_current_price()
        finally:
            config.PRICE_RETRY_COUNT = original

        self.assertIsNone(px)
        # 每次 attempt 失败都尝试 reconnect → 至少调用 1 次
        self.assertGreaterEqual(ex.reconnect.call_count, 1)

    def test_request_exception_triggers_reconnect(self):
        """reqMktData 抛任意异常 → 必须尝试 reconnect (即便最终仍失败)."""
        ex = self._make_executor()
        ex._poll_price_once = MagicMock(side_effect=RuntimeError("API broken"))
        ex.reconnect = MagicMock(return_value=False)

        original = config.PRICE_RETRY_COUNT
        config.PRICE_RETRY_COUNT = 2
        try:
            ex.get_current_price()
        finally:
            config.PRICE_RETRY_COUNT = original

        ex.reconnect.assert_called()

    def test_not_connected_triggers_reconnect_first(self):
        """is_connected=False → reconnect 后再 poll, reconnect 成功则后续轮询成功."""
        ex = self._make_executor()
        ex.ib.isConnected.return_value = False
        ex._poll_price_once = MagicMock(return_value=15.0)

        def reconnect_stub():
            ex.ib.isConnected.return_value = True
            ex._market_data_type_effective = 3
            return True
        ex.reconnect = MagicMock(side_effect=reconnect_stub)

        original = config.PRICE_RETRY_COUNT
        config.PRICE_RETRY_COUNT = 2
        try:
            px = ex.get_current_price()
        finally:
            config.PRICE_RETRY_COUNT = original

        self.assertEqual(px, 15.0)
        self.assertGreaterEqual(ex.reconnect.call_count, 1)


class TestExecuteEntryPriceUnavailable(unittest.TestCase):
    """get_current_price 返回 None 时, _execute_entry 必须 safe-fail —
    不下单, 不伪造成交, 写 EXECUTE_ENTRY_PRICE_UNAVAILABLE 风险事件."""

    def test_no_price_skips_order_and_logs_event(self):
        from grid_bot import GridBot
        bot = GridBot.__new__(GridBot)
        bot.executor = MagicMock()
        bot.executor.get_current_price.return_value = None
        bot.executor.is_connected.return_value = True
        bot.db = MagicMock()
        bot._base_position_shares = 0.0

        result = bot._execute_entry(MagicMock())

        self.assertFalse(result)
        bot.executor.place_market_order.assert_not_called()
        bot.db.log_risk_event.assert_called()
        first_arg = bot.db.log_risk_event.call_args[0][0]
        self.assertEqual(first_arg, "EXECUTE_ENTRY_PRICE_UNAVAILABLE")


class TestScanningImmediatelyHandsOffToWaiting(unittest.TestCase):
    """scanning 检测到入场条件后, 必须在同一 step 立即触发一次 _handle_waiting_entry.

    历史 bug: 转换到 WAITING_ENTRY 后只 return, 上层 main.py 立刻 sleep
    waiting_interval_sec(); 历史上 waiting_interval_sec()=strategy_interval_seconds()=4h,
    睡 4h 后才进 _handle_waiting_entry, 真正可入场的瞬时窗口已错过.

    本测试与上面的 TestConfigDynamicInterval 互补 — 即使 waiting interval 缩到 5min,
    也仍然存在 "刚发现信号 → 此刻 timing 满足 → 但要等 1 个 sleep 周期才尝试" 的延迟.
    立即 hand-off 可消除这个延迟.
    """

    def _make_bot(self, conditions_passed: bool, timing_passed: bool):
        from grid_bot import GridBot
        clock = MagicMock()
        clock.now.return_value = datetime(2026, 5, 5, 11, 0)

        sm = StateMachine(clock=clock)
        # 起点: SCANNING

        bot = GridBot.__new__(GridBot)
        bot.clock = clock
        bot.state_machine = sm
        bot.db = MagicMock()
        bot.db.db_path = ":memory:"
        bot.risk = MagicMock()
        bot.risk.check_trading_hours.return_value = True
        bot.data_fetcher = MagicMock()
        bot.data_fetcher.get_strategy_data.return_value = MagicMock(
            __len__=MagicMock(return_value=100)
        )
        bot.entry_filter = MagicMock()

        eval_mock = MagicMock()
        eval_mock.conditions_passed = conditions_passed
        eval_mock.timing_passed = timing_passed
        eval_mock.rejection_reasons = []
        eval_mock.current_price = 37.82
        eval_mock.adx_value = 15.0
        eval_mock.atr_pct = 0.035
        eval_mock.bb_width_pct = 0.10
        eval_mock.ema_value = 37.78
        bot.entry_filter.evaluate.return_value = eval_mock

        bot.executor = MagicMock()
        bot.grid = None
        bot._base_position_shares = 0.0
        bot._entry_execution_failures = 0
        bot._last_scan_time = None
        bot.strategy_df_days = 30
        bot._persist_all = lambda: None
        return bot

    def test_scanning_triggers_immediate_waiting_evaluation(self):
        """conditions_passed=True 的同一 step 内, _handle_waiting_entry 必须被调用."""
        bot = self._make_bot(conditions_passed=True, timing_passed=False)

        called = {"n": 0}
        original_handler = bot._handle_waiting_entry if hasattr(bot, "_handle_waiting_entry") else None

        def spy():
            called["n"] += 1
            # 不实际跑业务, 只验证被调用即可

        bot._handle_waiting_entry = spy

        bot._handle_scanning()
        self.assertEqual(called["n"], 1, "scanning→waiting 应立即触发一次 waiting 评估")

    def test_scanning_no_signal_does_not_call_waiting(self):
        """没有信号时不应触发 _handle_waiting_entry."""
        bot = self._make_bot(conditions_passed=False, timing_passed=False)
        bot._handle_waiting_entry = MagicMock()
        bot._handle_scanning()
        bot._handle_waiting_entry.assert_not_called()
        self.assertEqual(bot.state_machine.state, SystemState.SCANNING)


class TestWaitingEntryAutoReset(unittest.TestCase):
    """
    waiting_entry 自动恢复: _execute_entry 返回 False 时累计计数,
    达到 ENTRY_EXECUTION_MAX_FAILURES 则自动回 SCANNING, 不需要人工改 SQLite.
    """

    def _make_bot(self):
        """构造一个最小可用的 GridBot, 状态置于 WAITING_ENTRY."""
        from grid_bot import GridBot

        clock = MagicMock()
        clock.now.return_value = datetime(2026, 4, 28, 11, 0)

        sm = StateMachine(clock=clock)
        sm.transition_to(SystemState.WAITING_ENTRY,
                         "测试 fixture", now=clock.now())

        bot = GridBot.__new__(GridBot)
        bot.clock = clock
        bot.state_machine = sm
        bot.db = MagicMock()
        bot.db.db_path = ":memory:"
        bot.risk = MagicMock()
        bot.risk.check_trading_hours.return_value = True
        bot.data_fetcher = MagicMock()
        bot.data_fetcher.get_strategy_data.return_value = MagicMock()
        bot.entry_filter = MagicMock()

        eval_mock = MagicMock()
        eval_mock.conditions_passed = True
        eval_mock.rejection_reasons = []
        eval_mock.timing_passed = True
        bot.entry_filter.evaluate.return_value = eval_mock

        bot.executor = MagicMock()
        bot.grid = None
        bot._base_position_shares = 0.0
        bot._entry_execution_failures = 0
        bot.strategy_df_days = 30
        bot._persist_all = lambda: None  # 避免 fs 写入
        return bot

    def test_threshold_1_first_failure_returns_to_scanning(self):
        """ENTRY_EXECUTION_MAX_FAILURES=1: 一次失败立即回 SCANNING."""
        bot = self._make_bot()
        bot._execute_entry = MagicMock(return_value=False)
        original = config.ENTRY_EXECUTION_MAX_FAILURES
        config.ENTRY_EXECUTION_MAX_FAILURES = 1
        try:
            bot._handle_waiting_entry()
        finally:
            config.ENTRY_EXECUTION_MAX_FAILURES = original

        self.assertEqual(bot.state_machine.state, SystemState.SCANNING)
        self.assertEqual(bot._entry_execution_failures, 0)
        # 写了一条 WAITING_ENTRY_AUTO_RESET 风险事件
        called_types = [c[0][0] for c in bot.db.log_risk_event.call_args_list]
        self.assertIn("WAITING_ENTRY_AUTO_RESET", called_types)

    def test_threshold_2_first_stays_then_second_resets(self):
        """ENTRY_EXECUTION_MAX_FAILURES=2: 第一次仍 WAITING, 第二次回 SCANNING."""
        bot = self._make_bot()
        bot._execute_entry = MagicMock(return_value=False)
        original = config.ENTRY_EXECUTION_MAX_FAILURES
        config.ENTRY_EXECUTION_MAX_FAILURES = 2
        try:
            bot._handle_waiting_entry()
            self.assertEqual(bot.state_machine.state, SystemState.WAITING_ENTRY)
            self.assertEqual(bot._entry_execution_failures, 1)

            bot._handle_waiting_entry()
        finally:
            config.ENTRY_EXECUTION_MAX_FAILURES = original

        self.assertEqual(bot.state_machine.state, SystemState.SCANNING)
        self.assertEqual(bot._entry_execution_failures, 0)

    def test_success_resets_failure_counter(self):
        """成功执行入场后, 即使之前累计了失败次数也应清零."""
        bot = self._make_bot()
        bot._entry_execution_failures = 3
        bot._execute_entry = MagicMock(return_value=True)
        original = config.ENTRY_EXECUTION_MAX_FAILURES
        config.ENTRY_EXECUTION_MAX_FAILURES = 5
        try:
            bot._handle_waiting_entry()
        finally:
            config.ENTRY_EXECUTION_MAX_FAILURES = original

        self.assertEqual(bot._entry_execution_failures, 0)

    def test_check_entry_timeout_still_triggers_scanning(self):
        """新顺序下: 即使 timing 未到, 超过 ENTRY_MAX_WAIT_BARS 仍应回 SCANNING."""
        bot = self._make_bot()

        # 关键: 这条路径要求 conditions_passed=True 但 timing_passed=False,
        # 否则会先走 _execute_entry 分支, 不会到 timeout 检查.
        bot.entry_filter.evaluate.return_value.timing_passed = False
        bot.entry_filter.evaluate.return_value.rejection_reasons = [
            "价格$10.00 偏离EMA $9.00 达 2.00×ATR"
        ]

        # 把窗口起点拨回去, 让 check_entry_timeout 触发
        started = bot.state_machine.context.entry_window_started_at
        self.assertIsNotNone(started)
        far_future = (
            datetime.fromisoformat(started)
            + timedelta(hours=(config.ENTRY_MAX_WAIT_BARS + 1)
                              * config.strategy_interval_hours())
        )
        bot.clock.now.return_value = far_future

        # _execute_entry 不应被调用 — timing_passed=False, 走 timeout 路径
        bot._execute_entry = MagicMock(return_value=False)

        bot._handle_waiting_entry()

        self.assertEqual(bot.state_machine.state, SystemState.SCANNING)
        bot._execute_entry.assert_not_called()
        # 失败计数器没有动 (这条路径不属于执行失败)
        self.assertEqual(bot._entry_execution_failures, 0)

    def test_non_trading_hours_pushes_window_forward(self):
        """非交易时段必须把 entry_window_started_at 推到 now,
        否则跨夜/周末后 elapsed_bars 已经累计很大, 回到交易时段就立即超时.
        """
        bot = self._make_bot()
        bot.risk.check_trading_hours.return_value = False  # 非交易时段
        # 状态: WAITING_ENTRY 已建立; 假设是夜里 22:00 ET
        original_started = bot.state_machine.context.entry_window_started_at
        self.assertIsNotNone(original_started)

        # 时钟推到几小时后, 还是非交易时段
        later = datetime.fromisoformat(original_started) + timedelta(hours=10)
        bot.clock.now.return_value = later

        # 持久化打桩: 不写 fs, 但要让分支确实尝试 save_state
        bot.state_machine.save_state = MagicMock()

        bot._handle_waiting_entry()

        new_started = bot.state_machine.context.entry_window_started_at
        self.assertIsNotNone(new_started)
        self.assertEqual(new_started, later.isoformat(),
                         "非交易时段应把窗口起点推到 now")
        bot.state_machine.save_state.assert_called()
        # 仍然停留在 WAITING_ENTRY (没有触发超时)
        self.assertEqual(bot.state_machine.state, SystemState.WAITING_ENTRY)

    def test_timeout_boundary_epsilon_does_not_eat_window(self):
        """elapsed_bars 在 ENTRY_MAX_WAIT_BARS 边界上不应触发超时.

        重现 V49 实盘观察: T0 进 WAITING_ENTRY, 4h 后第一次重新评估时
        elapsed≈1.0 (ENTRY_MAX_WAIT_BARS=1), 因浮点/调度漂移可能极微略大于 1.0.
        新增的 ENTRY_TIMEOUT_EPSILON_BARS 必须吸收 sub-bar 量级的漂移,
        让 timing 有机会被评估.
        """
        bot = self._make_bot()
        bot.entry_filter.evaluate.return_value.timing_passed = False
        bot.entry_filter.evaluate.return_value.rejection_reasons = [
            "价格偏离EMA"
        ]

        started = bot.state_machine.context.entry_window_started_at
        self.assertIsNotNone(started)
        strategy_hours = config.strategy_interval_hours()
        # 漂移设为 epsilon 的 1/10, 远小于 epsilon → 必须被吸收, 不触发超时
        epsilon_bars = float(getattr(config, "ENTRY_TIMEOUT_EPSILON_BARS", 1e-6))
        drift_seconds = (epsilon_bars / 10.0) * strategy_hours * 3600
        boundary = (
            datetime.fromisoformat(started)
            + timedelta(hours=config.ENTRY_MAX_WAIT_BARS * strategy_hours)
            + timedelta(seconds=drift_seconds)
        )
        bot.clock.now.return_value = boundary
        bot._execute_entry = MagicMock(return_value=False)

        bot._handle_waiting_entry()

        # 期望仍然在 WAITING_ENTRY (epsilon 吸收了漂移)
        self.assertEqual(bot.state_machine.state, SystemState.WAITING_ENTRY)
        bot._execute_entry.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
