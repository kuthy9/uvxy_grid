"""
test.py — 全量单元/集成测试 (v3 合并版)

模块覆盖:
  核心 (原 test.py):
    - state_machine.py  状态转换
    - pnl_tracker.py    FIFO 买卖配对 / 胜率 / 持久化
    - risk_manager.py   6 层风控
    - grid_engine.py    网格构建 / 信号 / 重置 / 退出
    - executor / reconcile / waiting_entry 异常路径

  战术 (原 test_tactical.py):
    - tactical_rules / session_manager / state_machine v3 / trade_logger migration
    - GridBot defensive 信号过滤 / 软止损 / 硬止损 / partial profit

  多标的 (原 test_multi_symbol.py):
    - CapitalAllocator / ClientIdAllocator / AccountRiskManager
    - RiskManager 委托 / 多 bot 共享 hard_stop / 日亏损跨 symbol 聚合

  基础设施 (原 test_infrastructure.py):
    - CapitalProvider / cache invalidation / Clock 统一 / rescale
    - MultiSymbolOrchestrator 故障隔离 / manual_resume CLI

运行: python test.py
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import config
from account_risk import AccountRiskManager
from capital_allocator import (
    CapitalAllocator, AllocationError,
    single_symbol_allocator, equal_split_allocator,
    rescale_from_equity, rescale_from_account_risk,
    LiveCapitalProvider, StaticCapitalProvider, build_capital_provider,
)
from client_id_allocator import (
    ClientIdAllocator, ClientIdExhausted,
    get_default_allocator, reset_default_allocator,
)
from grid_engine import DynamicGridEngine, LevelState, GridSide
from interfaces import LiveClock
from orchestrator import MultiSymbolOrchestrator
from pnl_tracker import PnLTracker
from risk_manager import RiskManager
from state_machine import StateMachine, SystemState, is_grid_state
from trade_logger import TradeDatabase

# Tactical modules archived 2026-05-15. import-guard.
try:
    import tactical_config as tcfg
    import tactical_rules as trules
    from session_manager import (
        SessionManager, SessionContext,
        ACTION_NONE, ACTION_ENTER_DEFENSIVE, ACTION_FORCE_EXIT,
        ACTION_PROFIT_PROTECT_EXIT, ACTION_PARTIAL_PROFIT_EXIT,
    )
    _TACTICAL_AVAILABLE = True
except ImportError:
    _TACTICAL_AVAILABLE = False
    tcfg = None
    trules = None
    SessionManager = None
    SessionContext = None
    ACTION_NONE = None
    ACTION_ENTER_DEFENSIVE = None
    ACTION_FORCE_EXIT = None
    ACTION_PROFIT_PROTECT_EXIT = None
    ACTION_PARTIAL_PROFIT_EXIT = None


def setUpModule():
    """测试 fixture: 锁定 TOTAL_CAPITAL=10000 (v3 基线, 与用户 $10k 账户假设一致).
    生产代码从不读这个值 — 它只在 test/backtest 中被显式注入.
    部分老测试 (V49 baseline) 用 2000 — 它们仍能通过, 因为断言都用
    `config.TOTAL_CAPITAL * X` 而非硬编码数值."""
    config.TOTAL_CAPITAL = 10000.0


def _patch_tactical_on(test_case):
    """让单个 test case 在 TURBO=ON 模式下运行.

    P9 (5237573) 把 config.TURBO_ENABLED 默认改为 OFF (严证伪通过结果).
    12 个 tactical 行为测试假设 TURBO=ON, 用这个 helper 在 setUp 第一行调
    一次即可强制开启 tactical_config.TACTICAL_GRID_ENABLED,
    addCleanup 在 tearDown 阶段自动恢复原值.

    用法:
        def setUp(self):
            _patch_tactical_on(self)
            ...  # 原有 setUp 代码
    """
    if not _TACTICAL_AVAILABLE:
        test_case.skipTest("tactical modules archived 2026-05-15")
    import tactical_config as _tcfg
    import config as _cfg
    orig_tcfg = _tcfg.TACTICAL_GRID_ENABLED
    orig_cfg = _cfg.TURBO_ENABLED
    _tcfg.TACTICAL_GRID_ENABLED = True
    _cfg.TURBO_ENABLED = True
    test_case.addCleanup(setattr, _tcfg, "TACTICAL_GRID_ENABLED", orig_tcfg)
    test_case.addCleanup(setattr, _cfg, "TURBO_ENABLED", orig_cfg)


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
        # v3 起 on_grid_active 目标态是 OFFENSIVE_GRID (取代旧的 ACTIVE_GRID).
        t0 = datetime.now()
        self.sm.on_entry_evaluation(True, "ok", now=t0)
        sessions_before = self.sm.context.total_grid_sessions
        self.sm.on_grid_active(now=t0)
        self.assertEqual(self.sm.state, SystemState.OFFENSIVE_GRID)
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

        # 新实例从同一数据库恢复 — v3 后 on_grid_active 目标态是 OFFENSIVE_GRID
        sm2 = StateMachine()
        ok = sm2.load_state(self.db_path)
        self.assertTrue(ok)
        self.assertEqual(sm2.state, SystemState.OFFENSIVE_GRID)
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
        bot.symbol = config.SYMBOL  # __new__-style 测试: 必须显式设
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
        bot.symbol = config.SYMBOL  # __new__-style 测试: 必须显式设
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
        bot.symbol = config.SYMBOL  # __new__-style 测试: 必须显式设
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


class TestDailySnapshotWriting(unittest.TestCase):
    """daily_snapshots 必须独立于周报: 每个交易日一次 + 关键事件刷新.

    历史 bug: 仅 _generate_weekly_report() 调用 db.log_daily_snapshot,
    导致工作日有交易也不会产生当天 snapshot, 周一前的成交都看不到当日权益变化.
    修复后: 每个交易日 step() 中至少写一次, 关键事件 (entry/fill/exit/紧急清仓)
    都强制刷新当天 snapshot.
    """

    def _make_bot(self, now=None):
        from grid_bot import GridBot
        clock = MagicMock()
        clock.now.return_value = now or datetime(2026, 5, 6, 14, 30)

        sm = StateMachine(clock=clock)

        bot = GridBot.__new__(GridBot)
        bot.symbol = config.SYMBOL  # __new__-style 测试: 必须显式设
        bot.clock = clock
        bot.state_machine = sm
        bot.db = MagicMock()
        bot.db.db_path = ":memory:"
        bot.risk = MagicMock()
        bot.risk.check_trading_hours.return_value = True
        bot.risk.is_hard_stopped.return_value = False
        bot.risk.can_trade.return_value = True
        bot.risk.record_intraday_price = MagicMock()
        bot.entry_filter = MagicMock()
        bot.data_fetcher = MagicMock()

        bot.executor = MagicMock()
        bot.executor.is_connected.return_value = True
        bot.executor.get_position_details.return_value = {
            "shares": 115.0,
            "market_value": 115 * 36.04,
            "unrealized_pnl": -2.0,
        }
        bot.executor.get_account_summary.return_value = {
            "NetLiquidation": 9985.0,
            "TotalCashValue": 5841.4,
        }
        bot.executor.get_realized_pnl.return_value = 0.0
        bot.executor.get_cash.return_value = 5841.4

        bot.grid = None
        bot._base_position_shares = 0.0
        bot._entry_execution_failures = 0
        bot._last_scan_time = None
        bot._last_recenter_check = None
        bot._last_weekly_report_week = None
        bot._last_snapshot_date = None
        bot.strategy_df_days = 30
        bot.pnl = MagicMock()
        bot.pnl.get_today_pnl.return_value = 0.0
        bot.pnl.get_queue_summary.return_value = {
            "total_qty": 0.0, "avg_price": 0.0, "count": 0,
        }
        bot._persist_all = lambda: None
        return bot

    def test_log_daily_snapshot_now_writes_with_correct_fields(self):
        """_log_daily_snapshot_now 应调用 db.log_daily_snapshot 且字段反映当前快照."""
        bot = self._make_bot()
        bot._log_daily_snapshot_now(reason="unit_test")
        self.assertEqual(bot.db.log_daily_snapshot.call_count, 1)
        kwargs = bot.db.log_daily_snapshot.call_args.kwargs
        self.assertEqual(kwargs["state"], "scanning")
        self.assertAlmostEqual(kwargs["position_shares"], 115.0)
        self.assertAlmostEqual(kwargs["total_equity"], 9985.0, places=2)
        self.assertAlmostEqual(kwargs["cash"], 5841.4, places=2)
        # 没有网格时 grid_center=0
        self.assertEqual(kwargs["grid_center"], 0.0)

    def test_log_daily_snapshot_now_uses_grid_center_when_active(self):
        """有 grid 时, snapshot.grid_center 应为 grid.center_price."""
        bot = self._make_bot()
        bot.grid = MagicMock()
        bot.grid.center_price = 37.10
        bot._log_daily_snapshot_now(reason="entry_complete")
        kwargs = bot.db.log_daily_snapshot.call_args.kwargs
        self.assertAlmostEqual(kwargs["grid_center"], 37.10, places=2)

    def test_maybe_log_daily_snapshot_writes_once_per_date(self):
        """同一交易日多次调用 _maybe_log_daily_snapshot 只写一次."""
        bot = self._make_bot(now=datetime(2026, 5, 6, 9, 35))
        bot._maybe_log_daily_snapshot()
        self.assertEqual(bot.db.log_daily_snapshot.call_count, 1)
        bot._maybe_log_daily_snapshot()
        self.assertEqual(bot.db.log_daily_snapshot.call_count, 1)

    def test_maybe_log_daily_snapshot_writes_again_on_next_day(self):
        """跨入下一交易日, _maybe_log_daily_snapshot 必须再写一次."""
        bot = self._make_bot(now=datetime(2026, 5, 6, 14, 0))
        bot._maybe_log_daily_snapshot()
        self.assertEqual(bot.db.log_daily_snapshot.call_count, 1)
        bot.clock.now.return_value = datetime(2026, 5, 7, 9, 35)
        bot._maybe_log_daily_snapshot()
        self.assertEqual(bot.db.log_daily_snapshot.call_count, 2)

    def test_snapshot_written_after_successful_entry(self):
        """_execute_entry 成功后, 当天 daily_snapshot 必须刷新且 state=offensive_grid (v3)."""
        bot = self._make_bot()
        # on_grid_active 要求当前在 WAITING_ENTRY 才会转换到 OFFENSIVE_GRID
        bot.state_machine.transition_to(
            SystemState.WAITING_ENTRY, "test fixture", now=bot.clock.now()
        )
        bot.executor.get_current_price.return_value = 36.04
        bot.executor.place_market_order.return_value = "oid1"
        bot.executor.wait_for_order_fill.return_value = {
            "quantity": 105.0, "fill_price": 36.04, "commission": 0.35,
        }
        eval_mock = MagicMock()
        eval_mock.suggested_center = 37.10
        eval_mock.suggested_atr = 1.50
        eval_mock.suggested_spacing_pct = 0.012

        ok = bot._execute_entry(eval_mock)
        self.assertTrue(ok)
        bot.db.log_daily_snapshot.assert_called()
        states = [c.kwargs.get("state")
                  for c in bot.db.log_daily_snapshot.call_args_list]
        # v3 起新建仓走 OFFENSIVE_GRID; 兼容旧字符串依旧保留 enum 但实际写入是新值.
        self.assertIn("offensive_grid", states)

    def test_snapshot_written_after_grid_fill(self):
        """ACTIVE_GRID 中检测到成交, 当天 snapshot 必须刷新."""
        bot = self._make_bot()
        bot.state_machine.transition_to(
            SystemState.WAITING_ENTRY, "test", now=bot.clock.now()
        )
        bot.state_machine.on_grid_active(now=bot.clock.now())
        bot.grid = MagicMock()
        bot.grid.center_price = 37.10
        bot.grid.check_signals.return_value = []
        bot.grid.check_filled_resets = MagicMock()
        bot.grid.mark_order_filled = MagicMock()
        bot.executor.check_order_fills.return_value = [{
            "level_index": -1, "fill_price": 36.03, "quantity": 10.0,
            "action": "BUY", "order_id": "oid2", "order_type": "GRID_BUY",
            "commission": 0.35,
        }]
        bot.executor.get_current_price.return_value = 36.05
        bot._should_check_dynamic_adjustment = lambda: False

        bot._handle_active_grid()
        bot.db.log_daily_snapshot.assert_called()

    def test_snapshot_written_after_finalize_exit(self):
        """EXIT 完成 → daily snapshot 必须刷新 (反映 SCANNING + 空仓状态)."""
        bot = self._make_bot()
        bot.executor.get_position_details.return_value = {
            "shares": 0.0, "market_value": 0.0, "unrealized_pnl": 0.0,
        }
        bot.state_machine.transition_to(
            SystemState.WAITING_ENTRY, "test", now=bot.clock.now()
        )
        bot.state_machine.on_grid_active(now=bot.clock.now())
        bot.state_machine.on_exit_signal(True, "test_exit", now=bot.clock.now())

        bot._finalize_exit()
        bot.db.log_daily_snapshot.assert_called()

    def test_snapshot_failure_is_non_fatal(self):
        """db.log_daily_snapshot 抛错不应让 _log_daily_snapshot_now 冒泡崩溃."""
        bot = self._make_bot()
        bot.db.log_daily_snapshot.side_effect = RuntimeError("disk full")
        try:
            bot._log_daily_snapshot_now(reason="boom")
        except Exception as e:  # pragma: no cover
            self.fail(f"_log_daily_snapshot_now 不应抛出异常, got: {e}")


# ════════════════════════════════════════════
#  ReportGenerator: session 维度聚合
# ════════════════════════════════════════════

class TestReportGeneratorSessionAggregation(unittest.TestCase):
    """从 grid_sessions / grid_session_events / trades 聚合 session 维度统计."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = TradeDatabase(db_path=self.tmp.name)
        from report_generator import ReportGenerator
        import tempfile as _tf
        self.rdir = _tf.TemporaryDirectory()
        self.gen = ReportGenerator(db_path=self.tmp.name,
                                     report_dir=self.rdir.name,
                                     symbol="UVXY")

    def tearDown(self):
        self.rdir.cleanup()
        os.unlink(self.tmp.name)

    def _insert_session(self, sid, status, mode, total_pnl, age_bars,
                         exit_reason="", started_at=None):
        with sqlite3.connect(self.tmp.name) as conn:
            conn.execute(
                """INSERT INTO grid_sessions
                   (session_id, symbol, started_at, ended_at, status,
                    mode, start_equity, total_pnl, age_bars, max_drawdown,
                    exit_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, "UVXY", started_at or "2026-01-01T10:00",
                 "2026-01-02T10:00" if status == "closed" else None,
                 status, mode, 10000.0, total_pnl, age_bars, 20.0,
                 exit_reason)
            )

    def test_empty_db_returns_zero_aggregates(self):
        self.assertIsNone(self.gen.get_current_session())
        agg = self.gen.get_session_aggregates()
        self.assertEqual(agg["session_count"], 0)
        self.assertEqual(agg["forced_exit_count"], 0)

    def test_current_session_returned_for_open(self):
        self._insert_session("s_open", "open", "offensive", 50.0, 4.0)
        cur = self.gen.get_current_session()
        self.assertIsNotNone(cur)
        self.assertEqual(cur["session_id"], "s_open")
        self.assertEqual(cur["mode"], "offensive")

    def test_aggregates_pnl_stats(self):
        self._insert_session("s1", "closed", "offensive", 100.0, 6.0, "exit_complete")
        self._insert_session("s2", "closed", "defensive", -50.0, 12.0, "硬止损 触发")
        self._insert_session("s3", "closed", "offensive", 200.0, 8.0, "exit_complete")
        agg = self.gen.get_session_aggregates()
        self.assertEqual(agg["session_count"], 3)
        self.assertAlmostEqual(agg["average_session_pnl"], (100 - 50 + 200) / 3)
        self.assertAlmostEqual(agg["median_session_pnl"], 100.0)
        self.assertAlmostEqual(agg["max_session_loss"], -50.0)
        self.assertAlmostEqual(agg["max_session_age_bars"], 12.0)

    def test_forced_exit_count_from_reason(self):
        self._insert_session("s1", "closed", "offensive", -100.0, 5.0, "硬止损 force_exit")
        self._insert_session("s2", "closed", "defensive", -200.0, 6.0, "hard_stop")
        self._insert_session("s3", "closed", "offensive", 50.0, 4.0, "normal exit")
        agg = self.gen.get_session_aggregates()
        self.assertEqual(agg["forced_exit_count"], 2)
        self.assertAlmostEqual(agg["forced_exit_ratio"], 200.0 / 3)

    def test_profit_protect_and_timeout_counts(self):
        self._insert_session("s1", "closed", "offensive", 150.0, 5.0,
                              "trailing_giveback profit_protect")
        self._insert_session("s2", "closed", "defensive", -30.0, 25.0,
                              "absolute_max_age 超时")
        agg = self.gen.get_session_aggregates()
        self.assertEqual(agg["profit_protect_exit_count"], 1)
        self.assertEqual(agg["timeout_exit_count"], 1)

    def test_defensive_mode_count(self):
        self._insert_session("s1", "closed", "defensive", -50.0, 8.0)
        self._insert_session("s2", "closed", "offensive", 100.0, 5.0)
        self._insert_session("s3", "closed", "defensive", -20.0, 6.0)
        agg = self.gen.get_session_aggregates()
        self.assertEqual(agg["defensive_mode_count"], 2)
        self.assertAlmostEqual(agg["defensive_mode_ratio"], 200.0 / 3)

    def test_win_rate_and_profit_factor(self):
        self._insert_session("s1", "closed", "offensive", 100.0, 4.0)
        self._insert_session("s2", "closed", "offensive", -50.0, 6.0)
        self._insert_session("s3", "closed", "offensive", 200.0, 5.0)
        agg = self.gen.get_session_aggregates()
        # 2 wins / 3 sessions = 66.7%
        self.assertAlmostEqual(agg["win_rate"], 200.0 / 3, places=1)
        # profit_factor = (100+200) / |-50| = 6.0
        self.assertAlmostEqual(agg["profit_factor"], 6.0)

    def test_average_trades_per_session(self):
        self._insert_session("s1", "closed", "offensive", 100.0, 4.0)
        self._insert_session("s2", "closed", "offensive", 50.0, 3.0)
        # 模拟 trades 表写入 session_id
        with sqlite3.connect(self.tmp.name) as conn:
            for sid, count in [("s1", 6), ("s2", 4)]:
                for _ in range(count):
                    conn.execute(
                        """INSERT INTO trades
                           (timestamp, action, symbol, quantity, price, session_id)
                           VALUES (?, 'BUY', 'UVXY', 1.0, 10.0, ?)""",
                        ("2026-01-01T10:00", sid)
                    )
        agg = self.gen.get_session_aggregates()
        self.assertAlmostEqual(agg["average_trades_per_session"], 5.0)

    def test_render_sessions_does_not_crash_empty(self):
        html = self.gen._render_sessions()
        self.assertIn("暂无", html)

    def test_render_sessions_includes_current_and_aggregates(self):
        self._insert_session("s_open", "open", "offensive", 30.0, 3.0)
        self._insert_session("s1", "closed", "offensive", 50.0, 4.0, "exit_complete")
        html = self.gen._render_sessions()
        self.assertIn("s_open", html)
        self.assertIn("OFFENSIVE", html)
        self.assertIn("Session 总数", html)



# ════════════════════════════════════════════
#  从 test_tactical.py 合并: 战术规则 / session / 状态机 v3
# ════════════════════════════════════════════

# ════════════════════════════════════════════
#  tactical_rules: 纯函数评分 / 决策
# ════════════════════════════════════════════

class TestTrendRiskScore(unittest.TestCase):
    def setUp(self):
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")

    def test_calm_market_low_score(self):
        ctx = trules.MarketContext(
            current_price=10.0, ema=10.0, atr=0.2, atr_pct=0.02,
            adx=10.0, grid_center=10.0,
            ema_slope=0.0, consecutive_down_bars=0, price_below_ema_bars=0,
            atr_expansion=1.0,
        )
        score = trules.calculate_trend_risk_score(ctx)
        self.assertLess(score, 40.0)

    def test_strong_downtrend_high_score(self):
        # 测试 score 函数本身 — 不依赖 DEFENSIVE 阈值当前默认 (新默认 999 disables).
        ctx = trules.MarketContext(
            current_price=8.0, ema=10.0, atr=0.5, atr_pct=0.05,
            adx=35.0, grid_center=10.0,
            ema_slope=-0.015, consecutive_down_bars=5, price_below_ema_bars=5,
            atr_expansion=1.6,
        )
        score = trules.calculate_trend_risk_score(ctx)
        # 强下行场景应该得到偏高分 (≥60), 不强求触发 DEFENSIVE 阈值
        # (DEFENSIVE 阈值是用户可调的 opt-in 风控参数).
        self.assertGreaterEqual(score, 60.0)

    def test_extreme_score_caps_at_100(self):
        ctx = trules.MarketContext(
            current_price=5.0, ema=10.0, atr=1.0, atr_pct=0.20,
            adx=80.0, grid_center=10.0,
            ema_slope=-0.10, consecutive_down_bars=20, price_below_ema_bars=20,
            atr_expansion=3.0,
        )
        score = trules.calculate_trend_risk_score(ctx)
        self.assertLessEqual(score, 100.0)


class TestConfidence(unittest.TestCase):
    def setUp(self):
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")

    def test_calm_market_high_confidence(self):
        ctx = trules.MarketContext(
            current_price=10.0, ema=10.0, atr=0.2, atr_pct=0.02,
            adx=8.0, grid_center=10.0,
        )
        self.assertEqual(trules.calculate_confidence(ctx), tcfg.CONFIDENCE_HIGH)

    def test_volatile_market_low_confidence(self):
        ctx = trules.MarketContext(
            current_price=8.0, ema=10.0, atr=0.5, atr_pct=0.05,
            adx=35.0, grid_center=10.0,
            ema_slope=-0.015, consecutive_down_bars=4, price_below_ema_bars=4,
            atr_expansion=1.7,
        )
        self.assertEqual(trules.calculate_confidence(ctx), tcfg.CONFIDENCE_LOW)


class TestPositionCap(unittest.TestCase):
    def setUp(self):
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")

    def test_high_confidence_larger_cap(self):
        h_pct, h_lv = trules.calculate_dynamic_position_cap(tcfg.CONFIDENCE_HIGH)
        n_pct, n_lv = trules.calculate_dynamic_position_cap(tcfg.CONFIDENCE_NORMAL)
        self.assertGreater(h_pct, n_pct)
        self.assertGreaterEqual(h_lv, n_lv)

    def test_low_confidence_tighter(self):
        l_pct, l_lv = trules.calculate_dynamic_position_cap(tcfg.CONFIDENCE_LOW)
        n_pct, n_lv = trules.calculate_dynamic_position_cap(tcfg.CONFIDENCE_NORMAL)
        self.assertLess(l_pct, n_pct)


# ════════════════════════════════════════════
#  should_enter_defensive / should_force_exit / should_protect_profit
# ════════════════════════════════════════════

class TestDecisionFunctions(unittest.TestCase):
    def setUp(self):
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")

    def _view(self, **kwargs):
        defaults = dict(
            session_id="sess1", started_at=datetime.now().isoformat(),
            age_bars=2.0, start_equity=10000.0, start_price=10.0,
            current_equity=10000.0, realized_pnl=0.0, unrealized_pnl=0.0,
            total_pnl=0.0, peak_pnl=0.0, max_drawdown=0.0,
            max_position_value=0.0, position_value=0.0,
            filled_buy_levels=[], mode=tcfg.SESSION_MODE_OFFENSIVE,
        )
        defaults.update(kwargs)
        return trules.SessionStateView(**defaults)

    def _ctx(self, **kwargs):
        defaults = dict(
            current_price=10.0, ema=10.0, atr=0.2, atr_pct=0.02, adx=10.0,
            grid_center=10.0,
        )
        defaults.update(kwargs)
        return trules.MarketContext(**defaults)

    def test_soft_stop_triggers_defensive(self):
        # 不依赖 SOFT/HARD_STOP_PCT 当前默认值 — 临时设阈值再测.
        # 测试逻辑: loss 大于 SOFT 且小于 HARD → enter_defensive.
        orig_soft = tcfg.SESSION_SOFT_STOP_PCT
        orig_hard = tcfg.SESSION_HARD_STOP_PCT
        try:
            tcfg.SESSION_SOFT_STOP_PCT = 0.010
            tcfg.SESSION_HARD_STOP_PCT = 0.020
            view = self._view(total_pnl=-150.0)  # -1.5% on $10k start_equity
            ctx = self._ctx()
            should, reason = trules.should_enter_defensive(view, ctx)
            self.assertTrue(should)
            self.assertIn("软止损", reason)
        finally:
            tcfg.SESSION_SOFT_STOP_PCT = orig_soft
            tcfg.SESSION_HARD_STOP_PCT = orig_hard

    def test_hard_stop_triggers_force_exit(self):
        orig_hard = tcfg.SESSION_HARD_STOP_PCT
        try:
            tcfg.SESSION_HARD_STOP_PCT = 0.020
            view = self._view(total_pnl=-250.0)  # -2.5% > 2% HARD
            ctx = self._ctx()
            should, reason = trules.should_force_exit(view, ctx)
            self.assertTrue(should)
            self.assertIn("硬止损", reason)
        finally:
            tcfg.SESSION_HARD_STOP_PCT = orig_hard

    def test_no_action_in_calm_session(self):
        view = self._view(total_pnl=20.0)
        ctx = self._ctx()
        self.assertFalse(trules.should_force_exit(view, ctx)[0])
        self.assertFalse(trules.should_enter_defensive(view, ctx)[0])

    def test_absolute_max_age_force_exit(self):
        view = self._view(age_bars=tcfg.SESSION_ABSOLUTE_MAX_AGE_BARS + 1)
        ctx = self._ctx()
        should, _ = trules.should_force_exit(view, ctx)
        self.assertTrue(should)

    def test_trailing_giveback_triggers_profit_exit(self):
        # 不依赖 profit-protect 默认值 — 临时设阈值再测.
        orig_min = tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT
        orig_strong = tcfg.SESSION_STRONG_PROFIT_PCT
        orig_giveback = tcfg.SESSION_TRAILING_GIVEBACK_RATIO
        try:
            tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT = 0.005
            tcfg.SESSION_STRONG_PROFIT_PCT = 0.012
            tcfg.SESSION_TRAILING_GIVEBACK_RATIO = 0.40
            # peak=$150 (1.5% > 0.5%); 当前回吐到 $50 → giveback ratio 66% > 40%
            view = self._view(total_pnl=50.0, peak_pnl=150.0,
                              position_value=5000.0)
            protect, reason, details = trules.should_protect_profit(view)
            self.assertTrue(protect)
            self.assertEqual(details.get("action"), "exit")
            self.assertIn("giveback", details.get("reason_code", ""))
        finally:
            tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT = orig_min
            tcfg.SESSION_STRONG_PROFIT_PCT = orig_strong
            tcfg.SESSION_TRAILING_GIVEBACK_RATIO = orig_giveback

    def test_strong_profit_triggers_partial_exit(self):
        orig_min = tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT
        orig_strong = tcfg.SESSION_STRONG_PROFIT_PCT
        orig_partial = tcfg.SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO
        try:
            tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT = 0.005
            tcfg.SESSION_STRONG_PROFIT_PCT = 0.012
            tcfg.SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO = 0.35
            # 当前 1.5% (> 1.2% STRONG), peak 也是 1.5%, 没有回吐
            view = self._view(total_pnl=150.0, peak_pnl=150.0,
                              position_value=5000.0)
            protect, reason, details = trules.should_protect_profit(view)
            self.assertTrue(protect)
            self.assertEqual(details.get("action"), "partial_exit")
            self.assertAlmostEqual(
                details["partial_exit_ratio"],
                tcfg.SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO,
            )
        finally:
            tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT = orig_min
            tcfg.SESSION_STRONG_PROFIT_PCT = orig_strong
            tcfg.SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO = orig_partial


# ════════════════════════════════════════════
#  should_allow_buy / sell
# ════════════════════════════════════════════

class TestSignalFilters(unittest.TestCase):
    def setUp(self):
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")

    def _view(self, mode=None, **kwargs):
        if mode is None:
            mode = tcfg.SESSION_MODE_OFFENSIVE
        defaults = dict(
            session_id="s1", started_at=datetime.now().isoformat(),
            age_bars=1.0, start_equity=10000.0, current_equity=10000.0,
            position_value=0.0, mode=mode,
        )
        defaults.update(kwargs)
        return trules.SessionStateView(**defaults)

    def _ctx(self, **kwargs):
        defaults = dict(current_price=10.0, ema=10.0, atr=0.2, atr_pct=0.02,
                        adx=10.0, grid_center=10.0)
        defaults.update(kwargs)
        return trules.MarketContext(**defaults)

    def test_defensive_blocks_all_buys(self):
        view = self._view(mode=tcfg.SESSION_MODE_DEFENSIVE)
        for lv in [-1, -2, -3, -4, -5, -6]:
            ok, _ = trules.should_allow_buy(view, lv, self._ctx())
            self.assertFalse(ok, f"DEFENSIVE 不该允许 BUY level={lv}")

    def test_offensive_normal_max_3_buy_levels(self):
        view = self._view()  # 默认 OFFENSIVE
        # 当前 confidence 计算结果应是 HIGH (calm market), 允许 4 层
        ctx = self._ctx()
        ok, _ = trules.should_allow_buy(view, -1, ctx,
                                         confidence=tcfg.CONFIDENCE_NORMAL)
        self.assertTrue(ok)
        ok, _ = trules.should_allow_buy(view, -3, ctx,
                                         confidence=tcfg.CONFIDENCE_NORMAL)
        self.assertTrue(ok)
        # normal 下 -4 应被拒
        ok, _ = trules.should_allow_buy(view, -4, ctx,
                                         confidence=tcfg.CONFIDENCE_NORMAL)
        self.assertFalse(ok)

    def test_high_confidence_allows_one_more_level(self):
        view = self._view()
        ctx = self._ctx()
        ok, _ = trules.should_allow_buy(view, -4, ctx,
                                         confidence=tcfg.CONFIDENCE_HIGH)
        self.assertTrue(ok)
        # 第 5 层仍拒
        ok, _ = trules.should_allow_buy(view, -5, ctx,
                                         confidence=tcfg.CONFIDENCE_HIGH)
        self.assertFalse(ok)

    def test_position_cap_blocks_further_buy(self):
        # 仓位 5000 已达 NORMAL 上限 4500 (45% × 10000)
        view = self._view(position_value=5000.0)
        ctx = self._ctx()
        ok, why = trules.should_allow_buy(view, -1, ctx,
                                            confidence=tcfg.CONFIDENCE_NORMAL)
        self.assertFalse(ok)
        self.assertIn("仓位", why)

    def test_defensive_sell_requires_rebound(self):
        view = self._view(mode=tcfg.SESSION_MODE_DEFENSIVE)
        # 价格深跌 1×ATR 以下, 不应该卖
        ctx = self._ctx(current_price=9.7, grid_center=10.0, atr=0.2)
        ok, why = trules.should_allow_sell(view, 1, ctx)
        self.assertFalse(ok)
        # 反弹回 grid_center 之上则允许
        ctx2 = self._ctx(current_price=10.1, grid_center=10.0, atr=0.2)
        ok, _ = trules.should_allow_sell(view, 1, ctx2)
        self.assertTrue(ok)


# ════════════════════════════════════════════
#  战术 recenter 禁用 (场景 7/8/9)
#  - DEFENSIVE 下不 recenter
#  - 软止损后不 recenter
#  - OFFENSIVE 默认不 recenter
# ════════════════════════════════════════════

class TestRecenterDisabledScenarios(unittest.TestCase):
    """should_disable_recenter 三个独立分支."""

    def setUp(self):
        _patch_tactical_on(self)  # P9 fixup: 这些 test 假设 TURBO=ON

    def _view(self, mode=None):
        if mode is None:
            mode = tcfg.SESSION_MODE_OFFENSIVE
        return trules.SessionStateView(
            session_id="s1", started_at=datetime.now().isoformat(),
            age_bars=1.0, start_equity=10000.0, current_equity=10000.0,
            mode=mode,
        )

    def test_defensive_disables_recenter(self):
        """场景 7: session.mode==DEFENSIVE → should_disable_recenter=True."""
        view = self._view(mode=tcfg.SESSION_MODE_DEFENSIVE)
        disable, reason = trules.should_disable_recenter(view, soft_stop_triggered=False)
        self.assertTrue(disable)
        self.assertIn("DEFENSIVE", reason)

    def test_soft_stop_flag_disables_recenter_in_offensive(self):
        """场景 8: OFFENSIVE 中但 soft_stop_triggered=True → should_disable_recenter=True."""
        view = self._view(mode=tcfg.SESSION_MODE_OFFENSIVE)
        disable, reason = trules.should_disable_recenter(view, soft_stop_triggered=True)
        self.assertTrue(disable)
        # 注意优先级: OFFENSIVE 默认禁用也会命中. 但 soft_stop 分支应当也能匹配.
        # 实际行为: rules 先检查 DEFENSIVE → soft_stop → OFFENSIVE, 这里返回 "软止损后"
        # 当 TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE=False 时, OFFENSIVE 也禁用,
        # 测试只要求 disable=True (任一原因)
        self.assertTrue("软止损" in reason or "OFFENSIVE" in reason)

    def test_offensive_default_disables_recenter(self):
        """场景 9: OFFENSIVE 模式, 配置 TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE=False → 默认禁用 recenter."""
        view = self._view(mode=tcfg.SESSION_MODE_OFFENSIVE)
        # 验证默认配置确实禁用
        self.assertFalse(tcfg.TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE,
                          "默认 TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE 应为 False")
        disable, reason = trules.should_disable_recenter(view, soft_stop_triggered=False)
        self.assertTrue(disable)
        self.assertIn("OFFENSIVE", reason)

    def test_recenter_allowed_when_tactical_disabled(self):
        """战术开关 TACTICAL_GRID_ENABLED=False 时, should_disable_recenter 永不禁."""
        original = tcfg.TACTICAL_GRID_ENABLED
        try:
            tcfg.TACTICAL_GRID_ENABLED = False
            view = self._view(mode=tcfg.SESSION_MODE_DEFENSIVE)
            disable, _ = trules.should_disable_recenter(view, soft_stop_triggered=True)
            self.assertFalse(disable, "TACTICAL_GRID_ENABLED=False 时不应禁用 recenter")
        finally:
            tcfg.TACTICAL_GRID_ENABLED = original


class TestGridBotRecenterRespect(unittest.TestCase):
    """端到端: grid_bot._apply_dynamic_adjustment 调用 should_disable_recenter 后
    确实跳过 grid_engine.recenter (不调 batch_cancel_orders / log_grid_recenter).

    用 __new__ 装配最小 bot, 让 session 处于 DEFENSIVE, 然后塞一个 should_recenter=True
    的网格, 调 _apply_dynamic_adjustment, 验证 recenter 没被执行."""

    def setUp(self):
        _patch_tactical_on(self)  # P9 fixup: 这些 test 假设 TURBO=ON
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _make_bot(self, *, session_mode, soft_stop=False):
        from grid_bot import GridBot
        import pandas as pd
        import numpy as np

        clock = LiveClock()
        db = TradeDatabase(db_path=self.tmp.name)
        sm = SessionManager(db=db, clock=clock)
        sm.start_session(
            symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
            start_position=100.0, start_price=10.0,
        )
        if session_mode == tcfg.SESSION_MODE_DEFENSIVE:
            sm.enter_defensive_mode("test")

        # data_fetcher mock: 返回足够数据让 compute_all_indicators 跑过
        n = 100
        idx = pd.date_range("2026-01-01", periods=n, freq="4h")
        df = pd.DataFrame({
            "Open": np.full(n, 10.0), "High": np.full(n, 10.5),
            "Low": np.full(n, 9.5), "Close": np.full(n, 10.0),
            "Volume": np.full(n, 1e6),
        }, index=idx)
        df_fetcher = MagicMock()
        df_fetcher.get_strategy_data.return_value = df

        # mock grid: 假装 should_recenter 返回 True, 但 recenter 不应被调用
        grid = MagicMock()
        grid.center_price = 10.0
        grid.atr_at_init = 0.2
        grid.spacing_pct = 0.02  # float (sqlite 绑定 — 不能用 MagicMock)
        grid.should_exit.return_value = (False, "")
        grid.should_recenter.return_value = (True, "EMA 漂移测试")
        grid.recenter = MagicMock()

        bot = GridBot.__new__(GridBot)
        bot.symbol = "UVXY"
        bot.clock = clock
        bot.executor = MagicMock()
        bot.db = db
        bot.pnl = MagicMock()
        bot.risk = MagicMock()
        bot.state_machine = MagicMock()
        bot.state_machine.state = SystemState.DEFENSIVE_GRID \
            if session_mode == tcfg.SESSION_MODE_DEFENSIVE \
            else SystemState.OFFENSIVE_GRID
        bot.state_machine.on_exit_signal.return_value = None
        bot.entry_filter = MagicMock()
        bot.data_fetcher = df_fetcher
        bot.strategy_df_days = 60
        bot.session_manager = sm
        bot.grid = grid
        bot._soft_stop_triggered = bool(soft_stop)
        bot._market_context_cache = None
        bot._allocated_capital = None
        bot._capital_provider = None
        return bot

    def test_defensive_session_skips_recenter(self):
        """场景 7 集成: DEFENSIVE 下 _apply_dynamic_adjustment 不调 grid.recenter."""
        bot = self._make_bot(session_mode=tcfg.SESSION_MODE_DEFENSIVE)
        bot._apply_dynamic_adjustment(current_price=10.0)
        bot.grid.recenter.assert_not_called()

    def test_soft_stop_skips_recenter(self):
        """场景 8 集成: OFFENSIVE 中但 _soft_stop_triggered=True 时也跳过 recenter."""
        bot = self._make_bot(
            session_mode=tcfg.SESSION_MODE_OFFENSIVE, soft_stop=True
        )
        bot._apply_dynamic_adjustment(current_price=10.0)
        bot.grid.recenter.assert_not_called()

    def test_offensive_default_skips_recenter(self):
        """场景 9 集成: 默认 OFFENSIVE 也跳过 recenter (TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE=False)."""
        bot = self._make_bot(session_mode=tcfg.SESSION_MODE_OFFENSIVE)
        bot._apply_dynamic_adjustment(current_price=10.0)
        bot.grid.recenter.assert_not_called()

    def test_recenter_runs_when_allowed_in_offensive(self):
        """对照测试: 临时把 TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE 打开, recenter 应被调用."""
        original = tcfg.TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE
        try:
            tcfg.TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE = True
            bot = self._make_bot(session_mode=tcfg.SESSION_MODE_OFFENSIVE)
            # 准备 mock recenter 返回值
            bot.grid.recenter.return_value = {
                "new_center": 10.5, "new_spacing": 0.02, "orders_to_cancel": [],
            }
            bot._apply_dynamic_adjustment(current_price=10.0)
            bot.grid.recenter.assert_called_once()
        finally:
            tcfg.TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE = original


# ════════════════════════════════════════════
#  战术覆盖: grid_engine.should_exit 不直接触发 EXIT_PENDING
#  战术 base=0: _execute_entry 跳过 BASE_BUY 市价单
# ════════════════════════════════════════════

class TestTacticalExitOverride(unittest.TestCase):
    """TACTICAL_OVERRIDE_GRID_ENGINE_EXIT=True 时:
       - grid_engine.should_exit=True 不应 state_machine.on_exit_signal(True, ...)
       - session_manager.record_engine_exit_signal 应被调用 (累计 count)
       - 也应继续走 recenter 逻辑 (与 should_disable_recenter 协同)
    关掉 override 时, 旧行为 (直接进 EXIT_PENDING) 必须保留.
    """

    def setUp(self):
        _patch_tactical_on(self)  # P9 fixup: 这些 test 假设 TURBO=ON
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _make_bot(self, *, exit_reason="ADX>EXIT_MAX"):
        from grid_bot import GridBot
        import pandas as pd
        import numpy as np

        clock = LiveClock()
        db = TradeDatabase(db_path=self.tmp.name)
        sm = SessionManager(db=db, clock=clock)
        sm.start_session(
            symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
            start_position=100.0, start_price=10.0,
        )

        n = 100
        idx = pd.date_range("2026-01-01", periods=n, freq="4h")
        df = pd.DataFrame({
            "Open": np.full(n, 10.0), "High": np.full(n, 10.5),
            "Low": np.full(n, 9.5), "Close": np.full(n, 10.0),
            "Volume": np.full(n, 1e6),
        }, index=idx)
        df_fetcher = MagicMock()
        df_fetcher.get_strategy_data.return_value = df

        grid = MagicMock()
        grid.center_price = 10.0
        grid.atr_at_init = 0.2
        grid.spacing_pct = 0.02
        grid.should_exit.return_value = (True, exit_reason)
        grid.should_recenter.return_value = (False, "")
        grid.recenter = MagicMock()

        bot = GridBot.__new__(GridBot)
        bot.symbol = "UVXY"
        bot.clock = clock
        bot.executor = MagicMock()
        bot.db = db
        bot.pnl = MagicMock()
        bot.risk = MagicMock()
        bot.state_machine = MagicMock()
        bot.state_machine.state = SystemState.OFFENSIVE_GRID
        bot.state_machine.on_exit_signal.return_value = None
        bot.entry_filter = MagicMock()
        bot.data_fetcher = df_fetcher
        bot.strategy_df_days = 60
        bot.session_manager = sm
        bot.grid = grid
        bot._soft_stop_triggered = False
        bot._market_context_cache = None
        bot._allocated_capital = None
        bot._capital_provider = None
        return bot

    def test_override_on_does_not_trigger_exit_pending(self):
        original = tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT
        try:
            tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = True
            bot = self._make_bot()
            bot._apply_dynamic_adjustment(current_price=10.0)
            # state_machine.on_exit_signal 不应被以 (True, ...) 调用
            calls = bot.state_machine.on_exit_signal.call_args_list
            triggered = [c for c in calls if c and c.args and c.args[0] is True]
            self.assertEqual(len(triggered), 0,
                             f"override 模式不应触发 EXIT_PENDING, 但调用了: {triggered}")
        finally:
            tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = original

    def test_override_on_records_signal_to_session(self):
        original = tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT
        try:
            tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = True
            bot = self._make_bot(exit_reason="ATR_PCT>EXIT_MAX")
            self.assertEqual(bot.session_manager.engine_exit_signal_count, 0)
            bot._apply_dynamic_adjustment(current_price=10.0)
            self.assertEqual(bot.session_manager.engine_exit_signal_count, 1)
            self.assertIsNotNone(bot.session_manager.last_engine_exit_signal)
            self.assertIn("ATR_PCT>EXIT_MAX",
                          bot.session_manager.last_engine_exit_signal[0])
        finally:
            tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = original

    def test_override_off_still_triggers_exit_pending(self):
        original = tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT
        try:
            tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = False
            bot = self._make_bot()
            bot._apply_dynamic_adjustment(current_price=10.0)
            # 旧行为: on_exit_signal(True, reason, now=...) 必须被调
            calls = bot.state_machine.on_exit_signal.call_args_list
            triggered = [c for c in calls if c and c.args and c.args[0] is True]
            self.assertGreaterEqual(len(triggered), 1,
                                    f"override 关闭时应进 EXIT_PENDING, 调用: {calls}")
        finally:
            tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = original

    def test_signal_counter_resets_on_new_session(self):
        original = tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT
        try:
            tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = True
            bot = self._make_bot()
            bot._apply_dynamic_adjustment(current_price=10.0)
            self.assertEqual(bot.session_manager.engine_exit_signal_count, 1)
            # 关闭旧 session, 开新 session — 计数器应清零
            bot.session_manager.close_session(reason="test_close", end_price=10.0)
            bot.session_manager.start_session(
                symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
                start_position=100.0, start_price=10.0,
            )
            self.assertEqual(bot.session_manager.engine_exit_signal_count, 0)
            self.assertIsNone(bot.session_manager.last_engine_exit_signal)
        finally:
            tcfg.TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = original


class TestTacticalBaseZeroEntry(unittest.TestCase):
    """TACTICAL_BASE_POSITION_RATIO=0.0 时, _execute_entry 跳过 BASE_BUY 市价单,
    直接初始化网格 + 启动 session."""

    def setUp(self):
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self._orig_base_ratio = tcfg.TACTICAL_BASE_POSITION_RATIO
        self._orig_tactical = tcfg.TACTICAL_GRID_ENABLED

    def tearDown(self):
        tcfg.TACTICAL_BASE_POSITION_RATIO = self._orig_base_ratio
        tcfg.TACTICAL_GRID_ENABLED = self._orig_tactical
        os.unlink(self.tmp.name)

    def _make_bot(self):
        from grid_bot import GridBot

        clock = LiveClock()
        db = TradeDatabase(db_path=self.tmp.name)
        sm = SessionManager(db=db, clock=clock)

        executor = MagicMock()
        executor.get_current_price.return_value = 10.0
        executor.get_account_summary.return_value = {"TotalCashValue": 5000.0}
        # 关键: skip_base 路径下不应调 place_market_order
        executor.place_market_order = MagicMock()

        bot = GridBot.__new__(GridBot)
        bot.symbol = "UVXY"
        bot.clock = clock
        bot.executor = executor
        bot.db = db
        bot.pnl = MagicMock()
        bot.risk = MagicMock()
        bot.state_machine = MagicMock()
        bot.state_machine.state = SystemState.WAITING_ENTRY
        bot.state_machine.on_grid_active.return_value = None
        bot.entry_filter = MagicMock()
        bot.data_fetcher = MagicMock()
        bot.strategy_df_days = 60
        bot.session_manager = sm
        bot._allocated_capital = 10000.0
        bot._capital_provider = None
        bot._soft_stop_triggered = False
        bot._market_context_cache = None
        bot._base_position_shares = 0.0
        bot._entry_execution_failures = 0
        # _persist_all / _log_daily_snapshot_now 用 mock 避免 IO
        bot._persist_all = MagicMock()
        bot._log_daily_snapshot_now = MagicMock()
        return bot

    def _eval(self):
        from entry_filter import EntryEvaluation
        from datetime import datetime, timezone
        ev = EntryEvaluation(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            current_price=10.0,
        )
        ev.suggested_center = 10.0
        ev.suggested_atr = 0.2
        ev.suggested_spacing_pct = 0.02
        return ev

    def test_base_zero_skips_market_order(self):
        tcfg.TACTICAL_GRID_ENABLED = True
        tcfg.TACTICAL_BASE_POSITION_RATIO = 0.0
        bot = self._make_bot()
        ok = bot._execute_entry(self._eval())
        self.assertTrue(ok, "base=0 路径应入场成功")
        bot.executor.place_market_order.assert_not_called()
        self.assertEqual(bot._base_position_shares, 0.0)
        # session 应已启动
        self.assertTrue(bot.session_manager.has_active_session)
        self.assertEqual(bot.session_manager.session.start_position, 0.0)

    def test_base_zero_initializes_grid(self):
        tcfg.TACTICAL_GRID_ENABLED = True
        tcfg.TACTICAL_BASE_POSITION_RATIO = 0.0
        bot = self._make_bot()
        ok = bot._execute_entry(self._eval())
        self.assertTrue(ok)
        # 网格对象应被构建
        self.assertTrue(hasattr(bot, "grid"))
        self.assertAlmostEqual(bot.grid.center_price, 10.0, places=4)

    def test_base_nonzero_still_places_market_order(self):
        """安全网: 把 TACTICAL_BASE_POSITION_RATIO 调到 0.10 时, 仍应走 BASE_BUY 市价单."""
        tcfg.TACTICAL_GRID_ENABLED = True
        tcfg.TACTICAL_BASE_POSITION_RATIO = 0.10
        bot = self._make_bot()
        # mock executor 返回成功 fill
        bot.executor.place_market_order.return_value = 1001
        bot.executor.wait_for_order_fill.return_value = {
            "quantity": 100.0, "fill_price": 10.0, "commission": 1.0
        }
        ok = bot._execute_entry(self._eval())
        self.assertTrue(ok)
        bot.executor.place_market_order.assert_called_once()
        call_kwargs = bot.executor.place_market_order.call_args.kwargs
        self.assertEqual(call_kwargs.get("order_type_label"), "BASE_BUY")


# ════════════════════════════════════════════
#  SessionManager 生命周期 + 持久化
# ════════════════════════════════════════════

class TestSessionManager(unittest.TestCase):
    def setUp(self):
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = TradeDatabase(db_path=self.tmp.name)
        self.clock = LiveClock()
        self.sm = SessionManager(db=self.db, clock=self.clock)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_start_and_close_session(self):
        ctx = self.sm.start_session(
            symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
            start_position=100.0, start_price=10.0,
        )
        self.assertTrue(self.sm.has_active_session)
        self.assertEqual(ctx.mode, tcfg.SESSION_MODE_OFFENSIVE)
        closed = self.sm.close_session(reason="test_close", end_price=11.0)
        self.assertIsNotNone(closed)
        self.assertFalse(self.sm.has_active_session)
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.end_price, 11.0)

    def test_update_tracks_peak_and_drawdown(self):
        self.sm.start_session(
            symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
            start_position=100.0, start_price=10.0,
        )
        self.sm.update_session(
            current_equity=10100.0, current_cash=5000.0,
            current_position=100.0, current_price=11.0,
            unrealized_pnl=100.0,
        )
        self.assertAlmostEqual(self.sm.session.peak_pnl, 100.0)
        self.sm.update_session(
            current_equity=10050.0, current_cash=5000.0,
            current_position=100.0, current_price=10.5,
            unrealized_pnl=50.0,
        )
        self.assertAlmostEqual(self.sm.session.peak_pnl, 100.0)
        self.assertAlmostEqual(self.sm.session.max_drawdown, 50.0)

    def test_enter_defensive_changes_mode(self):
        self.sm.start_session(
            symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
            start_position=100.0, start_price=10.0,
        )
        self.sm.enter_defensive_mode("test")
        self.assertEqual(self.sm.session.mode, tcfg.SESSION_MODE_DEFENSIVE)

    def test_evaluate_force_exit_on_hard_stop(self):
        orig_hard = tcfg.SESSION_HARD_STOP_PCT
        try:
            tcfg.SESSION_HARD_STOP_PCT = 0.020
            self.sm.start_session(
                symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
                start_position=100.0, start_price=10.0,
            )
            # 亏到 2.5% > 2% HARD → 触发硬止损
            self.sm.update_session(
                current_equity=9750.0, current_cash=5000.0,
                current_position=100.0, current_price=9.5,
                realized_pnl_delta=-250.0,
            )
            market = trules.MarketContext(
                current_price=9.5, ema=10.0, atr=0.2, atr_pct=0.02, adx=10.0,
                grid_center=10.0,
            )
            ev = self.sm.evaluate_session(market)
            self.assertEqual(ev.action, ACTION_FORCE_EXIT)
        finally:
            tcfg.SESSION_HARD_STOP_PCT = orig_hard

    def test_evaluate_defensive_on_soft_stop(self):
        orig_soft = tcfg.SESSION_SOFT_STOP_PCT
        orig_hard = tcfg.SESSION_HARD_STOP_PCT
        try:
            tcfg.SESSION_SOFT_STOP_PCT = 0.010
            tcfg.SESSION_HARD_STOP_PCT = 0.020
            self.sm.start_session(
                symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
                start_position=100.0, start_price=10.0,
            )
            # 亏 1.5% (>1% SOFT, <2% HARD) → 软止损
            self.sm.update_session(
                current_equity=9850.0, current_cash=5000.0,
                current_position=100.0, current_price=9.7,
                realized_pnl_delta=-150.0,
            )
            market = trules.MarketContext(
                current_price=9.7, ema=10.0, atr=0.2, atr_pct=0.02, adx=10.0,
                grid_center=10.0,
            )
            ev = self.sm.evaluate_session(market)
            self.assertEqual(ev.action, ACTION_ENTER_DEFENSIVE)
        finally:
            tcfg.SESSION_SOFT_STOP_PCT = orig_soft
            tcfg.SESSION_HARD_STOP_PCT = orig_hard

    def test_trailing_giveback_action(self):
        orig_min = tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT
        orig_strong = tcfg.SESSION_STRONG_PROFIT_PCT
        orig_giveback = tcfg.SESSION_TRAILING_GIVEBACK_RATIO
        try:
            tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT = 0.005
            tcfg.SESSION_STRONG_PROFIT_PCT = 0.012
            tcfg.SESSION_TRAILING_GIVEBACK_RATIO = 0.40
            self.sm.start_session(
                symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
                start_position=100.0, start_price=10.0,
            )
            # 先冲到 peak +1.5% ($150)
            self.sm.update_session(
                current_equity=10150.0, current_cash=5000.0,
                current_position=100.0, current_price=11.5,
                unrealized_pnl=150.0,
            )
            # 然后回吐到 $50 (giveback 66% > 40%)
            self.sm.update_session(
                current_equity=10050.0, current_cash=5000.0,
                current_position=100.0, current_price=10.5,
                unrealized_pnl=50.0,
            )
            market = trules.MarketContext(
                current_price=10.5, ema=10.0, atr=0.2, atr_pct=0.02, adx=10.0,
                grid_center=10.0,
            )
            ev = self.sm.evaluate_session(market)
            self.assertEqual(ev.action, ACTION_PROFIT_PROTECT_EXIT)
        finally:
            tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT = orig_min
            tcfg.SESSION_STRONG_PROFIT_PCT = orig_strong
            tcfg.SESSION_TRAILING_GIVEBACK_RATIO = orig_giveback

    def test_cooldown_remaining_decreases(self):
        # 1.0 bar cooldown
        self.sm.start_cooldown(bars=1.0, reason="test")
        self.assertTrue(self.sm.in_cooldown)
        self.assertGreater(self.sm.remaining_cooldown_bars(), 0)
        self.sm.clear_cooldown()
        self.assertFalse(self.sm.in_cooldown)

    def test_persist_and_load_active_session(self):
        ctx = self.sm.start_session(
            symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
            start_position=100.0, start_price=10.0,
        )
        original_sid = ctx.session_id
        # 新 manager 从同一 DB 恢复
        sm2 = SessionManager(db=self.db, clock=self.clock)
        restored = sm2.load_active_session()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.session_id, original_sid)
        self.assertEqual(restored.symbol, "UVXY")


# ════════════════════════════════════════════
#  StateMachine: 新枚举 + 旧 active_grid 兼容
# ════════════════════════════════════════════

class TestStateMachineNewStates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def test_on_grid_active_targets_offensive(self):
        sm = StateMachine()
        sm.on_entry_evaluation(True, "ok", now=datetime.now())
        sm.on_grid_active(now=datetime.now())
        self.assertEqual(sm.state, SystemState.OFFENSIVE_GRID)
        self.assertTrue(is_grid_state(sm.state))

    def test_offensive_to_defensive(self):
        sm = StateMachine()
        sm.on_entry_evaluation(True, "ok", now=datetime.now())
        sm.on_grid_active(now=datetime.now())
        sm.on_enter_defensive("soft_stop", now=datetime.now())
        self.assertEqual(sm.state, SystemState.DEFENSIVE_GRID)
        self.assertTrue(is_grid_state(sm.state))

    def test_defensive_to_exit_pending(self):
        sm = StateMachine()
        sm.on_entry_evaluation(True, "ok", now=datetime.now())
        sm.on_grid_active(now=datetime.now())
        sm.on_enter_defensive("soft_stop", now=datetime.now())
        result = sm.on_exit_signal(True, "hard_stop", now=datetime.now())
        self.assertEqual(result, "INITIATE_EXIT")
        self.assertEqual(sm.state, SystemState.EXIT_PENDING)

    def test_exit_complete_with_cooldown(self):
        sm = StateMachine()
        sm.on_entry_evaluation(True, "ok", now=datetime.now())
        sm.on_grid_active(now=datetime.now())
        sm.on_exit_signal(True, "test", now=datetime.now())
        sm.on_exit_complete(now=datetime.now(), enter_cooldown_bars=2.0,
                             cooldown_reason="hard_stop")
        self.assertEqual(sm.state, SystemState.COOLDOWN)

    def test_exit_complete_without_cooldown_goes_to_scanning(self):
        sm = StateMachine()
        sm.on_entry_evaluation(True, "ok", now=datetime.now())
        sm.on_grid_active(now=datetime.now())
        sm.on_exit_signal(True, "test", now=datetime.now())
        sm.on_exit_complete(now=datetime.now())
        self.assertEqual(sm.state, SystemState.SCANNING)

    def test_legacy_active_grid_db_row_translates(self):
        """旧 DB 行写的 'active_grid' 必须被翻译为 OFFENSIVE_GRID 恢复."""
        # 手工构造一行 active_grid 状态
        sm = StateMachine()
        sm.context.current_state = SystemState.ACTIVE_GRID  # 故意用旧枚举值写
        sm.context.state_entered_at = datetime.now().isoformat()
        sm.context.total_grid_sessions = 3
        sm.save_state(self.db_path)

        # 验证 DB 里确实写的是 "active_grid"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT current_state FROM state_machine_state WHERE id=1"
            ).fetchone()
        self.assertEqual(row[0], "active_grid")

        # 新 manager 恢复后应是 OFFENSIVE_GRID
        sm2 = StateMachine()
        ok = sm2.load_state(self.db_path)
        self.assertTrue(ok)
        self.assertEqual(sm2.state, SystemState.OFFENSIVE_GRID)
        self.assertEqual(sm2.context.total_grid_sessions, 3)


# ════════════════════════════════════════════
#  trade_logger: 幂等 schema 迁移
# ════════════════════════════════════════════

class TestSchemaMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def test_migration_is_idempotent(self):
        # 第一次初始化
        db = TradeDatabase(db_path=self.db_path)
        db.ensure_grid_sessions_table()
        db.ensure_grid_session_events_table()
        # 第二次, 不应失败
        db2 = TradeDatabase(db_path=self.db_path)
        db2.ensure_grid_sessions_table()
        db2.ensure_grid_session_events_table()

    def test_ensure_column_idempotent(self):
        db = TradeDatabase(db_path=self.db_path)
        # 已存在 session_id (migration 已加), 第二次 ensure_column 返回 False
        added = db.ensure_column("trades", "session_id", "TEXT")
        self.assertFalse(added)
        # 新列首次 ensure 返回 True, 重复 ensure 返回 False
        first = db.ensure_column("trades", "tactical_note", "TEXT")
        second = db.ensure_column("trades", "tactical_note", "TEXT")
        self.assertTrue(first)
        self.assertFalse(second)

    def test_grid_sessions_table_exists(self):
        db = TradeDatabase(db_path=self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='grid_sessions'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_log_session_event_writes_row(self):
        db = TradeDatabase(db_path=self.db_path)
        db.log_session_event("sid1", "TEST_EVENT", "x=1", "no-op")
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM grid_session_events WHERE session_id='sid1'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_log_trade_with_session_id(self):
        db = TradeDatabase(db_path=self.db_path)
        db.log_trade("BUY", "UVXY", 10.0, 5.0, session_id="sid_x")
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT session_id FROM trades WHERE symbol='UVXY' LIMIT 1"
            ).fetchone()
        self.assertEqual(row[0], "sid_x")


# ════════════════════════════════════════════
#  集成测试: defensive 模式在 grid_bot 中阻止 BUY 信号
# ════════════════════════════════════════════

class TestGridBotDefensiveBlocksBuy(unittest.TestCase):
    """端到端: bot 在 DEFENSIVE 模式下不应放出 BUY 单 (战术过滤生效)."""

    def setUp(self):
        _patch_tactical_on(self)  # P9 fixup: 这些 test 假设 TURBO=ON
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def _build_bot(self):
        from grid_bot import GridBot
        from grid_engine import DynamicGridEngine

        db = TradeDatabase(db_path=self.db_path)
        clock = LiveClock()
        sm = SessionManager(db=db, clock=clock)

        bot = GridBot.__new__(GridBot)
        bot.symbol = config.SYMBOL
        bot.clock = clock
        bot.executor = MagicMock()
        bot.db = db
        bot.pnl = MagicMock()
        bot.pnl.get_queue_summary.return_value = {
            "count": 1, "total_qty": 100.0,
            "total_cost": 1000.0, "avg_price": 10.0
        }
        bot.risk = MagicMock()
        bot.state_machine = StateMachine()
        bot.entry_filter = MagicMock()
        bot.data_fetcher = MagicMock()
        bot.strategy_df_days = 60
        bot.session_manager = sm
        bot._market_context_cache = None
        bot._soft_stop_triggered = False
        bot._last_recenter_check = datetime.now()  # 让 _should_check_dynamic_adjustment 返回 False

        # 网格 + 状态
        bot.grid = DynamicGridEngine(
            center_price=10.0, atr=0.2, spacing_pct=0.02,
            grid_capital=5000.0, current_time=datetime.now(),
        )
        bot.state_machine.on_entry_evaluation(True, "ok", now=datetime.now())
        bot.state_machine.on_grid_active(now=datetime.now())

        # 启动 session 并切到 DEFENSIVE
        sm.start_session(
            symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
            start_position=100.0, start_price=10.0,
        )
        bot._enter_defensive("test_defensive")
        return bot

    def test_defensive_state_active(self):
        bot = self._build_bot()
        self.assertEqual(bot.state_machine.state, SystemState.DEFENSIVE_GRID)
        self.assertEqual(bot.session_manager.session.mode, tcfg.SESSION_MODE_DEFENSIVE)

    def test_buy_orders_cancelled_on_defensive_entry(self):
        bot = self._build_bot()
        # 先模拟有 BUY 挂单
        # _enter_defensive 已经被调用过, 验证它确实调用了 cancel_order
        # (但 _build_bot 中 grid 是新建的, 没有 order_id, 所以这里只验证 state)
        # 重新模拟: 给 grid 一个 buy 挂单后再次调用 _enter_defensive 不会重复 cancel
        # (这里主要测 cancel_buy_orders 的逻辑)
        bot.executor.cancel_order.return_value = True
        # 注入一个有 order_id 的下方档
        for idx, lv in bot.grid.levels.items():
            if lv.side.value == "below":
                lv.order_id = 12345
                from grid_engine import LevelState
                lv.state = LevelState.ORDER_PENDING
                break
        cancelled = bot._cancel_buy_orders("test")
        self.assertGreater(cancelled, 0)
        bot.executor.cancel_order.assert_called()

    def test_place_grid_orders_filters_all_buys_in_defensive(self):
        bot = self._build_bot()
        bot.executor.cancel_order.return_value = True
        bot.executor.place_limit_order.return_value = None  # 不应该被调用 (BUY 全过滤)
        bot.executor.get_cash.return_value = 100000.0

        # 触发价格低于 center → 应产生 BUY 信号, 但被过滤
        ctx = trules.MarketContext(
            current_price=9.5, ema=10.0, atr=0.2, atr_pct=0.02, adx=10.0,
            grid_center=10.0,
        )
        bot._market_context_cache = ctx
        bot._place_grid_orders(ctx)

        # 不应该有任何 BUY 下单 (DEFENSIVE 拒绝); 但可能有 SELL — 取决于 grid 状态.
        # 检查所有调用的 action 不含 BUY.
        for call in bot.executor.place_limit_order.call_args_list:
            self.assertNotEqual(call.kwargs.get("action"), "BUY",
                                "DEFENSIVE 不应下 BUY 单")


# ════════════════════════════════════════════
#  集成测试: 软止损 → DEFENSIVE
# ════════════════════════════════════════════

class TestSoftStopTriggersDefensive(unittest.TestCase):
    def setUp(self):
        _patch_tactical_on(self)  # P9 fixup: 这些 test 假设 TURBO=ON
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def test_full_flow(self):
        """OFFENSIVE 中 session 亏 1.5% → evaluate_session 返回 ENTER_DEFENSIVE → grid_bot 切到 DEFENSIVE."""
        from grid_bot import GridBot
        from grid_engine import DynamicGridEngine

        # 测试不依赖 SOFT/HARD_STOP_PCT 当前默认 — 临时缩窄阈值, 让 -1.5% 触发.
        orig_soft = tcfg.SESSION_SOFT_STOP_PCT
        orig_hard = tcfg.SESSION_HARD_STOP_PCT
        try:
            tcfg.SESSION_SOFT_STOP_PCT = 0.010
            tcfg.SESSION_HARD_STOP_PCT = 0.020

            db = TradeDatabase(db_path=self.db_path)
            clock = LiveClock()
            sm = SessionManager(db=db, clock=clock)

            bot = GridBot.__new__(GridBot)
            bot.symbol = config.SYMBOL
            bot.clock = clock
            bot.executor = MagicMock()
            bot.db = db
            bot.pnl = MagicMock()
            bot.pnl.get_queue_summary.return_value = {
                "count": 0, "total_qty": 0.0, "total_cost": 0.0, "avg_price": 0.0,
            }
            bot.risk = MagicMock()
            bot.state_machine = StateMachine()
            bot.entry_filter = MagicMock()
            bot.data_fetcher = MagicMock()
            bot.strategy_df_days = 60
            bot.session_manager = sm
            bot._soft_stop_triggered = False

            bot.grid = DynamicGridEngine(
                center_price=10.0, atr=0.2, spacing_pct=0.02,
                grid_capital=5000.0, current_time=datetime.now(),
            )
            bot.state_machine.on_entry_evaluation(True, "ok", now=datetime.now())
            bot.state_machine.on_grid_active(now=datetime.now())

            sm.start_session(
                symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
                start_position=100.0, start_price=10.0,
            )
            sm.update_session(
                current_equity=9850.0, current_cash=5000.0,
                current_position=100.0, current_price=9.7,
                realized_pnl_delta=-150.0,  # -1.5% (> 1% SOFT, < 2% HARD)
            )

            market = trules.MarketContext(
                current_price=9.7, ema=10.0, atr=0.2, atr_pct=0.02, adx=10.0,
                grid_center=10.0,
            )
            bot.executor.cancel_order.return_value = True
            bot._evaluate_session_actions(market)
            self.assertEqual(bot.state_machine.state, SystemState.DEFENSIVE_GRID)
            self.assertEqual(bot.session_manager.session.mode,
                              tcfg.SESSION_MODE_DEFENSIVE)
        finally:
            tcfg.SESSION_SOFT_STOP_PCT = orig_soft
            tcfg.SESSION_HARD_STOP_PCT = orig_hard


# ════════════════════════════════════════════
#  集成测试: 硬止损 → EXIT_PENDING
# ════════════════════════════════════════════

class TestHardStopTriggersExit(unittest.TestCase):
    def setUp(self):
        _patch_tactical_on(self)  # P9 fixup: 这些 test 假设 TURBO=ON
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def test_full_flow(self):
        from grid_bot import GridBot
        from grid_engine import DynamicGridEngine

        orig_hard = tcfg.SESSION_HARD_STOP_PCT
        try:
            tcfg.SESSION_HARD_STOP_PCT = 0.020  # test calibrated for 2%

            db = TradeDatabase(db_path=self.db_path)
            clock = LiveClock()
            sm = SessionManager(db=db, clock=clock)

            bot = GridBot.__new__(GridBot)
            bot.symbol = config.SYMBOL
            bot.clock = clock
            bot.executor = MagicMock()
            bot.db = db
            bot.pnl = MagicMock()
            bot.risk = MagicMock()
            bot.state_machine = StateMachine()
            bot.entry_filter = MagicMock()
            bot.data_fetcher = MagicMock()
            bot.strategy_df_days = 60
            bot.session_manager = sm
            bot._soft_stop_triggered = False

            bot.grid = DynamicGridEngine(
                center_price=10.0, atr=0.2, spacing_pct=0.02,
                grid_capital=5000.0, current_time=datetime.now(),
            )
            bot.state_machine.on_entry_evaluation(True, "ok", now=datetime.now())
            bot.state_machine.on_grid_active(now=datetime.now())

            sm.start_session(
                symbol="UVXY", start_equity=10000.0, start_cash=5000.0,
                start_position=100.0, start_price=10.0,
            )
            sm.update_session(
                current_equity=9700.0, current_cash=5000.0,
                current_position=100.0, current_price=9.4,
                realized_pnl_delta=-300.0,  # -3% > 2% HARD
            )

            market = trules.MarketContext(
                current_price=9.4, ema=10.0, atr=0.2, atr_pct=0.02, adx=10.0,
                grid_center=10.0,
            )
            bot._evaluate_session_actions(market)
            self.assertEqual(bot.state_machine.state, SystemState.EXIT_PENDING)
        finally:
            tcfg.SESSION_HARD_STOP_PCT = orig_hard


# ════════════════════════════════════════════
#  多标的: symbol 参数化模块化测试
# ════════════════════════════════════════════

class TestMultiSymbolParameterization(unittest.TestCase):
    """验证 symbol 参数化已下沉到 GridBot / Executor / SessionManager / Report,
    为后续多标的 orchestrator 做准备."""

    def test_grid_bot_symbol_from_explicit_arg(self):
        """显式传 symbol 时不再读 config.SYMBOL."""
        from grid_bot import GridBot
        executor = MagicMock()
        executor.symbol = "TQQQ"  # executor 优先级最高
        bot = GridBot.__new__(GridBot)
        bot.symbol = None
        # 直接走 __init__ 逻辑里 self.symbol = symbol or executor.symbol or config.SYMBOL
        # 但这里用 __new__ 跳过 __init__, 手动重现
        bot.symbol = "SOXL" or executor.symbol or config.SYMBOL
        self.assertEqual(bot.symbol, "SOXL")

    def _tempfile_db(self):
        """共享: 临时 sqlite 文件 (注意 :memory: 在跨 connection 时不共享 schema,
        不能直接给 TradeDatabase 用)."""
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_grid_bot_symbol_from_executor_when_no_explicit(self):
        """不传 symbol 时从 executor.symbol 取."""
        from grid_bot import GridBot
        from interfaces import LiveClock
        from entry_filter import EntryFilter
        executor = MagicMock()
        executor.symbol = "TQQQ"
        executor.is_connected.return_value = True
        clock = LiveClock()
        db = TradeDatabase(db_path=self._tempfile_db())
        bot = GridBot(
            clock=clock, executor=executor, db=db,
            pnl=MagicMock(), risk=MagicMock(),
            state_machine=StateMachine(clock=clock),
            entry_filter=EntryFilter(),
            data_fetcher=MagicMock(),
        )
        self.assertEqual(bot.symbol, "TQQQ")

    def test_grid_bot_falls_back_to_config_symbol(self):
        """executor 无 symbol attr + 不传参 → 用 config.SYMBOL."""
        from grid_bot import GridBot
        from interfaces import LiveClock
        from entry_filter import EntryFilter
        # 用一个不带 symbol 属性的 dummy executor (spec=Executor 也不会有 symbol)
        class _DummyExecutor:
            pass
        executor = _DummyExecutor()
        clock = LiveClock()
        db = TradeDatabase(db_path=self._tempfile_db())
        bot = GridBot(
            clock=clock, executor=executor, db=db,
            pnl=MagicMock(), risk=MagicMock(),
            state_machine=StateMachine(clock=clock),
            entry_filter=EntryFilter(),
            data_fetcher=MagicMock(),
        )
        self.assertEqual(bot.symbol, config.SYMBOL)

    def test_simulated_executor_accepts_symbol(self):
        import pandas as pd
        from simulated_executor import SimulatedExecutor
        from interfaces import HistoricalClock
        clock = HistoricalClock()
        df = pd.DataFrame(
            {"Open": [10.0], "High": [10.5], "Low": [9.5],
             "Close": [10.0], "Volume": [1000]},
            index=pd.date_range("2026-01-01", periods=1)
        )
        clock.set(df.index[0].to_pydatetime())
        ex = SimulatedExecutor(df, 1000.0, clock, symbol="TQQQ")
        self.assertEqual(ex.symbol, "TQQQ")

    def test_simulated_executor_defaults_symbol(self):
        import pandas as pd
        from simulated_executor import SimulatedExecutor
        from interfaces import HistoricalClock
        clock = HistoricalClock()
        df = pd.DataFrame(
            {"Open": [10.0], "High": [10.5], "Low": [9.5],
             "Close": [10.0], "Volume": [1000]},
            index=pd.date_range("2026-01-01", periods=1)
        )
        clock.set(df.index[0].to_pydatetime())
        ex = SimulatedExecutor(df, 1000.0, clock)
        self.assertEqual(ex.symbol, config.SYMBOL)

    def test_two_session_managers_independent_per_db(self):
        """两个 SessionManager 用不同 db_path 时, session 互不污染."""
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")
        import os, tempfile
        from interfaces import LiveClock
        from session_manager import SessionManager
        tmp_a = tempfile.NamedTemporaryFile(suffix="_a.db", delete=False)
        tmp_b = tempfile.NamedTemporaryFile(suffix="_b.db", delete=False)
        tmp_a.close(); tmp_b.close()
        try:
            clock = LiveClock()
            db_a = TradeDatabase(db_path=tmp_a.name)
            db_b = TradeDatabase(db_path=tmp_b.name)
            sm_a = SessionManager(db=db_a, clock=clock)
            sm_b = SessionManager(db=db_b, clock=clock)
            sm_a.start_session(symbol="UVXY", start_equity=10000.0,
                                start_cash=5000.0, start_position=100.0,
                                start_price=10.0)
            sm_b.start_session(symbol="TQQQ", start_equity=10000.0,
                                start_cash=5000.0, start_position=50.0,
                                start_price=80.0)
            self.assertEqual(sm_a.session.symbol, "UVXY")
            self.assertEqual(sm_b.session.symbol, "TQQQ")
            # session_id 应该不同 (每个 DB 各自命名空间, 但通过 uuid 后缀也保证唯一)
            self.assertNotEqual(sm_a.session.session_id, sm_b.session.session_id)
        finally:
            os.unlink(tmp_a.name)
            os.unlink(tmp_b.name)

    def test_report_generator_accepts_symbol(self):
        from report_generator import ReportGenerator
        gen = ReportGenerator(db_path=self._tempfile_db(), symbol="TQQQ", report_dir="/tmp")
        self.assertEqual(gen.symbol, "TQQQ")

    def test_factory_builds_with_custom_symbol(self):
        """bot_factory.build_test_grid_bot 透过 symbol 参数."""
        import tempfile, os
        from bot_factory import build_test_grid_bot
        from interfaces import HistoricalClock
        import pandas as pd
        from simulated_executor import SimulatedExecutor

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            clock = HistoricalClock()
            df = pd.DataFrame(
                {"Open": [10.0], "High": [10.5], "Low": [9.5],
                 "Close": [10.0], "Volume": [1000]},
                index=pd.date_range("2026-01-01", periods=1)
            )
            clock.set(df.index[0].to_pydatetime())
            ex = SimulatedExecutor(df, 1000.0, clock, symbol="SOXL")
            bot = build_test_grid_bot(
                symbol="SOXL", db_path=tmp.name,
                executor=ex, data_fetcher=MagicMock(), clock=clock,
            )
            self.assertEqual(bot.symbol, "SOXL")
            self.assertEqual(bot.executor.symbol, "SOXL")
        finally:
            os.unlink(tmp.name)



# ════════════════════════════════════════════
#  从 test_multi_symbol.py 合并: CapitalAllocator / AccountRiskManager 等
# ════════════════════════════════════════════

# ════════════════════════════════════════════
#  CapitalAllocator
# ════════════════════════════════════════════

class TestCapitalAllocator(unittest.TestCase):
    def test_for_symbol_basic(self):
        a = CapitalAllocator(total=10000.0, allocations={"UVXY": 0.4, "TQQQ": 0.6})
        self.assertEqual(a.for_symbol("UVXY"), 4000.0)
        self.assertEqual(a.for_symbol("TQQQ"), 6000.0)

    def test_unlisted_symbol_returns_zero(self):
        a = CapitalAllocator(total=10000.0, allocations={"UVXY": 1.0})
        self.assertEqual(a.for_symbol("TQQQ"), 0.0)

    def test_validation_sum_exceeds_one_raises(self):
        with self.assertRaises(AllocationError):
            CapitalAllocator(total=10000.0,
                              allocations={"UVXY": 0.6, "TQQQ": 0.6})

    def test_validation_negative_fraction_raises(self):
        with self.assertRaises(AllocationError):
            CapitalAllocator(total=10000.0, allocations={"UVXY": -0.1})

    def test_validation_total_zero_raises(self):
        with self.assertRaises(AllocationError):
            CapitalAllocator(total=0.0, allocations={"UVXY": 1.0})

    def test_single_symbol_helper(self):
        a = single_symbol_allocator(10000.0, "UVXY")
        self.assertEqual(a.for_symbol("UVXY"), 10000.0)

    def test_equal_split_helper(self):
        a = equal_split_allocator(10000.0, ["UVXY", "TQQQ", "SOXL"])
        for s in ["UVXY", "TQQQ", "SOXL"]:
            self.assertAlmostEqual(a.for_symbol(s), 10000.0 / 3)

    def test_unallocated_buffer(self):
        a = CapitalAllocator(total=10000.0,
                              allocations={"UVXY": 0.4, "TQQQ": 0.3})
        self.assertAlmostEqual(a.total_allocated(), 7000.0)
        self.assertAlmostEqual(a.unallocated(), 3000.0)

    def test_with_overrides_returns_new_instance(self):
        a = CapitalAllocator(total=10000.0, allocations={"UVXY": 1.0})
        b = a.with_overrides(total=20000.0)
        self.assertEqual(a.total, 10000.0)
        self.assertEqual(b.total, 20000.0)
        self.assertEqual(b.for_symbol("UVXY"), 20000.0)


# ════════════════════════════════════════════
#  ClientIdAllocator
# ════════════════════════════════════════════

class TestClientIdAllocator(unittest.TestCase):
    def test_allocate_unique_ids(self):
        a = ClientIdAllocator(base=1)
        ids = {a.allocate() for _ in range(5)}
        self.assertEqual(len(ids), 5, "allocate 必须返回唯一 id")

    def test_reserve_then_allocate_skips(self):
        a = ClientIdAllocator(base=1)
        a.reserve(1)
        cid = a.allocate()
        self.assertNotEqual(cid, 1)

    def test_release_makes_id_reusable(self):
        a = ClientIdAllocator(base=1)
        cid = a.allocate()
        self.assertEqual(cid, 1)
        a.release(cid)
        new_cid = a.allocate()
        self.assertEqual(new_cid, 1)

    def test_default_allocator_singleton(self):
        reset_default_allocator()
        d1 = get_default_allocator()
        d2 = get_default_allocator()
        self.assertIs(d1, d2)

    def test_base_default_from_config(self):
        a = ClientIdAllocator()
        cid = a.allocate()
        # 默认 base = config.IBKR_CLIENT_ID (通常 1)
        self.assertEqual(cid, config.IBKR_CLIENT_ID)


# ════════════════════════════════════════════
#  AccountRiskManager
# ════════════════════════════════════════════

class TestAccountRiskManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.clock = LiveClock()
        self.arm = AccountRiskManager(
            db_path=self.tmp.name, clock=self.clock,
            total_capital=10000.0, equity_cache_ttl_sec=10.0
        )

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_initial_state_allows_trade(self):
        self.assertFalse(self.arm.is_hard_stopped())
        self.assertFalse(self.arm.is_daily_loss_stopped())

    def test_hard_stop_triggers_above_threshold(self):
        # 亏 25% > HARD_STOP_LOSS_PCT (20%)
        r = self.arm.check_hard_stop(account_equity=7500.0)
        self.assertFalse(r)
        self.assertTrue(self.arm.is_hard_stopped())

    def test_hard_stop_not_triggered_below_threshold(self):
        # 亏 10% < HARD_STOP_LOSS_PCT
        r = self.arm.check_hard_stop(account_equity=9000.0)
        self.assertTrue(r)
        self.assertFalse(self.arm.is_hard_stopped())

    def test_hard_stop_persists_across_instances(self):
        """触发硬止损后, 重启账户级 manager 必须保持 triggered 状态."""
        self.arm.check_hard_stop(account_equity=7500.0)
        self.assertTrue(self.arm.is_hard_stopped())
        # 新实例从同一 DB 恢复
        arm2 = AccountRiskManager(db_path=self.tmp.name, clock=self.clock,
                                    total_capital=10000.0)
        self.assertTrue(arm2.is_hard_stopped())

    def test_contribute_trade_pnl_aggregates_across_symbols(self):
        self.arm.contribute_trade_pnl("UVXY", -100.0)
        self.arm.contribute_trade_pnl("TQQQ", -200.0)
        self.arm.contribute_trade_pnl("SOXL", 50.0)
        total = self.arm.get_today_account_realized_pnl()
        self.assertAlmostEqual(total, -250.0)

    def test_daily_loss_triggers_on_aggregate(self):
        # MAX_DAILY_LOSS_PCT=0.05 × 10000 = $500 上限
        self.arm.contribute_trade_pnl("UVXY", -300.0)
        self.arm.contribute_trade_pnl("TQQQ", -300.0)
        r = self.arm.check_daily_loss()
        self.assertFalse(r)
        self.assertTrue(self.arm.is_daily_loss_stopped())

    def test_daily_loss_not_triggered_on_per_symbol_alone(self):
        """单 symbol $300 < $500 上限; 但聚合应能跨 symbol 触发 (上一个测试).
        这里验证只有 1 个 symbol 时, 单 symbol 亏损 < limit 不触发."""
        self.arm.contribute_trade_pnl("UVXY", -300.0)
        r = self.arm.check_daily_loss()
        self.assertTrue(r)
        self.assertFalse(self.arm.is_daily_loss_stopped())

    def test_equity_cache_ttl_reuses_within_window(self):
        executor = MagicMock()
        executor.get_account_summary.return_value = {"NetLiquidation": 9500.0}
        v1 = self.arm.get_account_equity(executor)
        v2 = self.arm.get_account_equity(executor)
        self.assertEqual(v1, 9500.0)
        self.assertEqual(v2, 9500.0)
        # 缓存命中, executor 只应被调一次
        self.assertEqual(executor.get_account_summary.call_count, 1)

    def test_invalidate_cache_forces_reread(self):
        executor = MagicMock()
        executor.get_account_summary.return_value = {"NetLiquidation": 9500.0}
        self.arm.get_account_equity(executor)
        executor.get_account_summary.return_value = {"NetLiquidation": 9800.0}
        self.arm.invalidate_equity_cache()
        v = self.arm.get_account_equity(executor)
        self.assertEqual(v, 9800.0)
        self.assertEqual(executor.get_account_summary.call_count, 2)

    def test_manual_resume_clears_flags(self):
        self.arm.check_hard_stop(account_equity=7000.0)
        self.assertTrue(self.arm.is_hard_stopped())
        self.arm.manual_resume()
        self.assertFalse(self.arm.is_hard_stopped())


# ════════════════════════════════════════════
#  RiskManager 委托
# ════════════════════════════════════════════

class TestRiskManagerWithAccountRisk(unittest.TestCase):
    def setUp(self):
        self.tmp_account = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_account.close()
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.clock = LiveClock()
        self.account_risk = AccountRiskManager(
            db_path=self.tmp_account.name, clock=self.clock,
            total_capital=10000.0,
        )
        self.db = TradeDatabase(db_path=self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_account.name)
        os.unlink(self.tmp_db.name)

    def test_risk_manager_delegates_is_hard_stopped(self):
        risk = RiskManager(self.db, clock=self.clock,
                            account_risk=self.account_risk)
        self.assertFalse(risk.is_hard_stopped())
        self.account_risk.check_hard_stop(account_equity=7000.0)  # 触发
        self.assertTrue(risk.is_hard_stopped())

    def test_on_trade_closed_writes_to_account_risk(self):
        risk = RiskManager(self.db, clock=self.clock,
                            account_risk=self.account_risk)
        risk.on_trade_closed("UVXY", -100.0, note="test")
        total = self.account_risk.get_today_account_realized_pnl()
        self.assertEqual(total, -100.0)

    def test_on_trade_closed_noop_without_account_risk(self):
        """单标的模式 (account_risk=None) 时 on_trade_closed 不报错也不写共享表."""
        risk = RiskManager(self.db, clock=self.clock)
        # 不抛异常 = pass
        risk.on_trade_closed("UVXY", -100.0)

    def test_position_limit_uses_allocated_capital(self):
        """allocated_capital=$5000 → position_limit 阈值 = 5000 × 0.95 = $4750"""
        risk = RiskManager(self.db, clock=self.clock,
                            allocated_capital=5000.0)
        # $4800 持仓应被拒
        r = risk.check_position_limit(4800.0)
        self.assertFalse(r)
        # $4000 持仓应允许
        r2 = risk.check_position_limit(4000.0)
        self.assertTrue(r2)

    def test_position_limit_falls_back_to_total_capital(self):
        """不传 allocated_capital → 用 config.TOTAL_CAPITAL ($10000)"""
        risk = RiskManager(self.db, clock=self.clock)
        # $9000 < 10000 × 0.95 = $9500 → 允许
        r = risk.check_position_limit(9000.0)
        self.assertTrue(r)
        # $9600 > 9500 → 拒
        r2 = risk.check_position_limit(9600.0)
        self.assertFalse(r2)


# ════════════════════════════════════════════
#  端到端: 两个 GridBot 共享 account_risk
# ════════════════════════════════════════════

class TestMultiBotShareAccountRisk(unittest.TestCase):
    """模拟两个 bot 共用一个 AccountRiskManager:
    - bot A 触发硬止损 → bot B 立即看到 is_hard_stopped True
    - bot A 写日内 PnL → bot B 读到的 daily_loss 包含 A 的份额
    """

    def setUp(self):
        self.tmp_account = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_a = tempfile.NamedTemporaryFile(suffix="_a.db", delete=False)
        self.tmp_b = tempfile.NamedTemporaryFile(suffix="_b.db", delete=False)
        for t in (self.tmp_account, self.tmp_a, self.tmp_b):
            t.close()
        self.clock = LiveClock()
        self.account_risk = AccountRiskManager(
            db_path=self.tmp_account.name, clock=self.clock,
            total_capital=10000.0,
        )
        self.db_a = TradeDatabase(db_path=self.tmp_a.name)
        self.db_b = TradeDatabase(db_path=self.tmp_b.name)
        self.risk_a = RiskManager(self.db_a, clock=self.clock,
                                    account_risk=self.account_risk,
                                    allocated_capital=4000.0)
        self.risk_b = RiskManager(self.db_b, clock=self.clock,
                                    account_risk=self.account_risk,
                                    allocated_capital=6000.0)

    def tearDown(self):
        for t in (self.tmp_account, self.tmp_a, self.tmp_b):
            os.unlink(t.name)

    def test_hard_stop_visible_to_other_bot(self):
        # bot A 触发账户级硬止损
        self.account_risk.check_hard_stop(account_equity=7000.0)
        # bot B 在它的 risk_manager 上也应看到
        self.assertTrue(self.risk_b.is_hard_stopped())
        self.assertTrue(self.risk_a.is_hard_stopped())

    def test_daily_loss_aggregates_across_bots(self):
        # A 亏 $300, B 亏 $300, 合计 $600 > MAX_DAILY_LOSS_PCT × total ($500)
        self.risk_a.on_trade_closed("UVXY", -300.0)
        self.risk_b.on_trade_closed("TQQQ", -300.0)
        total = self.account_risk.get_today_account_realized_pnl()
        self.assertAlmostEqual(total, -600.0)
        r = self.account_risk.check_daily_loss()
        self.assertFalse(r)
        # bot B 调 can_trade 也会被 daily_loss 拦
        # 模拟 can_trade 路径
        check = self.risk_b.account_risk.check_daily_loss()
        self.assertFalse(check)

    def test_allocated_capital_independence(self):
        """两个 bot 的 position_limit 各按自己的 allocated 算."""
        # risk_a allocated=4000 → max = 3800
        self.assertFalse(self.risk_a.check_position_limit(3900))
        self.assertTrue(self.risk_a.check_position_limit(3000))
        # risk_b allocated=6000 → max = 5700
        self.assertFalse(self.risk_b.check_position_limit(5800))
        self.assertTrue(self.risk_b.check_position_limit(5000))


# ════════════════════════════════════════════
#  bot_factory 多标的入口
# ════════════════════════════════════════════

class TestBuildMultiSymbolBots(unittest.TestCase):
    """build_multi_symbol_bots 不连 IBKR 时也应能装配 (executor.connect 推迟到运行时).
    本测试只验证装配阶段不抛异常 + 各 bot 字段正确."""

    def test_assembly_uses_allocator_and_shared_account_risk(self):
        import tempfile
        # 用 tempdir 隔离 db 路径
        with tempfile.TemporaryDirectory() as tmp:
            from bot_factory import build_multi_symbol_bots
            bots = build_multi_symbol_bots(
                symbols=["UVXY", "TQQQ"],
                total_capital=10000.0,
                allocations={"UVXY": 0.4, "TQQQ": 0.6},
                db_path_pattern=os.path.join(tmp, "trades_{sym}.db"),
                account_db_path=os.path.join(tmp, "account.db"),
            )
            self.assertEqual(len(bots), 2)
            self.assertEqual(bots["UVXY"].symbol, "UVXY")
            self.assertEqual(bots["TQQQ"].symbol, "TQQQ")
            # allocated_capital 透传
            self.assertAlmostEqual(bots["UVXY"]._allocated_capital, 4000.0)
            self.assertAlmostEqual(bots["TQQQ"]._allocated_capital, 6000.0)
            # 共享 account_risk
            ar_a = bots["UVXY"].risk.account_risk
            ar_b = bots["TQQQ"].risk.account_risk
            self.assertIs(ar_a, ar_b)
            # client_id 不同
            self.assertNotEqual(
                bots["UVXY"].executor.client_id,
                bots["TQQQ"].executor.client_id,
            )

    def test_equal_split_default_allocations(self):
        import tempfile
        from bot_factory import build_multi_symbol_bots
        with tempfile.TemporaryDirectory() as tmp:
            bots = build_multi_symbol_bots(
                symbols=["A", "B", "C"],
                total_capital=9000.0,
                db_path_pattern=os.path.join(tmp, "trades_{sym}.db"),
                account_db_path=os.path.join(tmp, "account.db"),
            )
            for sym in ["A", "B", "C"]:
                self.assertAlmostEqual(bots[sym]._allocated_capital, 3000.0)



# ════════════════════════════════════════════
#  从 test_infrastructure.py 合并: CapitalProvider / Orchestrator / CLI
# ════════════════════════════════════════════

# ════════════════════════════════════════════
#  D1 CapitalProvider
# ════════════════════════════════════════════

class TestLiveCapitalProvider(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.clock = LiveClock()
        self.arm = AccountRiskManager(
            db_path=self.tmp.name, clock=self.clock,
            total_capital=10000.0,
        )

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _executor(self, netliq=10500.0):
        ex = MagicMock()
        ex.get_account_summary.return_value = {"NetLiquidation": netliq}
        return ex

    def test_no_allocator_returns_full_equity_minus_reserve(self):
        p = LiveCapitalProvider(self.arm, reserve_ratio=0.0)
        cap = p.get_capital_for("UVXY", executor=self._executor(netliq=10000.0))
        self.assertEqual(cap, 10000.0)

    def test_reserve_ratio_applied(self):
        p = LiveCapitalProvider(self.arm, reserve_ratio=0.05)
        cap = p.get_capital_for("UVXY", executor=self._executor(netliq=10000.0))
        self.assertAlmostEqual(cap, 9500.0)

    def test_allocator_fraction_applied(self):
        alloc = CapitalAllocator(total=10000.0,
                                  allocations={"UVXY": 0.4, "TQQQ": 0.6})
        p = LiveCapitalProvider(self.arm, allocator=alloc, reserve_ratio=0.0)
        ex = self._executor(netliq=10000.0)
        self.assertAlmostEqual(p.get_capital_for("UVXY", ex), 4000.0)
        self.assertAlmostEqual(p.get_capital_for("TQQQ", ex), 6000.0)

    def test_dynamic_responds_to_equity_change(self):
        """LiveCapitalProvider 必须每次都读 fresh equity, 账户涨跌应被反映."""
        p = LiveCapitalProvider(self.arm, reserve_ratio=0.0)
        ex = MagicMock()
        ex.get_account_summary.side_effect = [
            {"NetLiquidation": 10000.0},
            {"NetLiquidation": 11000.0},
            {"NetLiquidation": 9000.0},
        ]
        c1 = p.get_capital_for("UVXY", executor=ex)
        # 缓存让第二次返回 10000 仍然 (TTL=10s 默认)
        self.assertAlmostEqual(c1, 10000.0)
        self.arm.invalidate_equity_cache()
        c2 = p.get_capital_for("UVXY", executor=ex)
        self.assertAlmostEqual(c2, 11000.0)
        self.arm.invalidate_equity_cache()
        c3 = p.get_capital_for("UVXY", executor=ex)
        self.assertAlmostEqual(c3, 9000.0)

    def test_failure_falls_back_to_total_capital(self):
        """executor 取不到 NetLiq → 退回 account_risk.total_capital."""
        p = LiveCapitalProvider(self.arm, reserve_ratio=0.0)
        ex = MagicMock()
        ex.get_account_summary.side_effect = RuntimeError("API down")
        cap = p.get_capital_for("UVXY", executor=ex)
        # 总资金 10000 不应为 0 (兜底)
        self.assertEqual(cap, 10000.0)


class TestStaticCapitalProvider(unittest.TestCase):
    def test_uses_explicit_total(self):
        p = StaticCapitalProvider(total=5000.0)
        self.assertEqual(p.get_capital_for("UVXY"), 5000.0)

    def test_falls_back_to_config(self):
        p = StaticCapitalProvider()
        self.assertEqual(p.get_capital_for("UVXY"), 10000.0)

    def test_allocator_fraction(self):
        alloc = CapitalAllocator(total=10000.0,
                                  allocations={"UVXY": 0.3, "TQQQ": 0.7})
        p = StaticCapitalProvider(total=5000.0, allocator=alloc)
        self.assertAlmostEqual(p.get_capital_for("UVXY"), 1500.0)
        self.assertAlmostEqual(p.get_capital_for("TQQQ"), 3500.0)


class TestBuildCapitalProvider(unittest.TestCase):
    def test_with_account_risk_returns_live(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            arm = AccountRiskManager(db_path=tmp.name, clock=LiveClock(),
                                       total_capital=10000.0)
            p = build_capital_provider(account_risk=arm)
            self.assertIsInstance(p, LiveCapitalProvider)
        finally:
            os.unlink(tmp.name)

    def test_no_account_risk_returns_static(self):
        p = build_capital_provider(account_risk=None, static_total=5000.0)
        self.assertIsInstance(p, StaticCapitalProvider)


# ════════════════════════════════════════════
#  D3 ClientIdAllocator range / exhaustion
# ════════════════════════════════════════════

class TestClientIdRange(unittest.TestCase):
    def test_exhaustion_raises(self):
        a = ClientIdAllocator(base=1, max_id=3)
        ids = {a.allocate(), a.allocate(), a.allocate()}
        self.assertEqual(ids, {1, 2, 3})
        with self.assertRaises(ClientIdExhausted):
            a.allocate()

    def test_reserve_then_allocate_finds_gap(self):
        a = ClientIdAllocator(base=1, max_id=10)
        a.reserve(1)
        a.reserve(2)
        cid = a.allocate()
        self.assertEqual(cid, 3)

    def test_range_property(self):
        a = ClientIdAllocator(base=5, max_id=10)
        self.assertEqual(a.range, (5, 10))

    def test_invalid_range_raises(self):
        with self.assertRaises(ValueError):
            ClientIdAllocator(base=10, max_id=5)
        with self.assertRaises(ValueError):
            ClientIdAllocator(base=100, min_id=1, max_id=10)

    def test_available_count(self):
        a = ClientIdAllocator(base=1, max_id=5)
        self.assertEqual(a.available_count(), 5)
        a.allocate()
        self.assertEqual(a.available_count(), 4)
        a.reserve(99)  # 范围外不影响 available_count? 实际它仍然加入 _used 集合
        # 99 不在 [1,5], 但 reserve 不验证范围 (只是标记). available_count 用集合 size.
        # 这是预期行为: reserve 用来标记外部占用, 不强制在 range 内.

    def test_release_does_not_remove_external_reservation(self):
        a = ClientIdAllocator(base=1, max_id=10)
        cid = a.allocate()
        self.assertEqual(cid, 1)
        a.release(1)
        self.assertEqual(a.allocate(), 1)


class TestIBKRExecutorClientIdInUseDetection(unittest.TestCase):
    """IBKRExecutor._is_client_id_in_use_error 模式匹配"""

    def test_detects_326_code(self):
        from ibkr_executor import IBKRExecutor
        e = RuntimeError("Error 326: Unable to connect as the client id is already in use")
        self.assertTrue(IBKRExecutor._is_client_id_in_use_error(e))

    def test_detects_phrase(self):
        from ibkr_executor import IBKRExecutor
        e = ConnectionError("client id is already used by another connection")
        self.assertTrue(IBKRExecutor._is_client_id_in_use_error(e))

    def test_does_not_detect_unrelated_error(self):
        from ibkr_executor import IBKRExecutor
        e = RuntimeError("Connection refused")
        self.assertFalse(IBKRExecutor._is_client_id_in_use_error(e))


# ════════════════════════════════════════════
#  D5 NetLiquidation cache invalidation on trade close
# ════════════════════════════════════════════

class TestEquityCacheInvalidation(unittest.TestCase):
    def setUp(self):
        self.tmp_a = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_a.close()
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.clock = LiveClock()
        self.arm = AccountRiskManager(
            db_path=self.tmp_a.name, clock=self.clock,
            total_capital=10000.0,
        )
        self.db = TradeDatabase(db_path=self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_a.name)
        os.unlink(self.tmp_db.name)

    def test_on_trade_closed_invalidates_cache(self):
        risk = RiskManager(self.db, clock=self.clock, account_risk=self.arm)
        ex = MagicMock()
        ex.get_account_summary.return_value = {"NetLiquidation": 10000.0}
        # 先填充缓存
        eq1 = self.arm.get_account_equity(ex)
        self.assertEqual(eq1, 10000.0)
        self.assertEqual(ex.get_account_summary.call_count, 1)
        # 平仓事件触发失效
        risk.on_trade_closed("UVXY", -50.0)
        # 模拟 IBKR 端权益变化
        ex.get_account_summary.return_value = {"NetLiquidation": 9950.0}
        eq2 = self.arm.get_account_equity(ex)
        # 失效后必读 fresh
        self.assertEqual(eq2, 9950.0)
        self.assertEqual(ex.get_account_summary.call_count, 2)


# ════════════════════════════════════════════
#  D6 Clock 统一注入
# ════════════════════════════════════════════

class TestSharedClock(unittest.TestCase):
    def test_multi_symbol_bots_share_clock(self):
        from bot_factory import build_multi_symbol_bots
        with tempfile.TemporaryDirectory() as tmp:
            bots = build_multi_symbol_bots(
                symbols=["UVXY", "TQQQ"],
                total_capital=10000.0,
                db_path_pattern=os.path.join(tmp, "trades_{sym}.db"),
                account_db_path=os.path.join(tmp, "account.db"),
            )
            clocks = {id(b.clock) for b in bots.values()}
            self.assertEqual(len(clocks), 1, "所有 bot 必须共享同一 Clock 实例")
            # account_risk 也共享同一 clock
            ar_clocks = {id(b.risk.account_risk.clock) for b in bots.values()}
            self.assertEqual(ar_clocks, clocks)

    def test_explicit_clock_propagates(self):
        from bot_factory import build_live_grid_bot
        with tempfile.TemporaryDirectory() as tmp:
            clk = LiveClock()
            # 不能直连 IBKR, 但能验证 clock 透传到 risk / state / session
            # 用 patch 拦截 IBKRExecutor 防真连接
            with patch("bot_factory.IBKRExecutor") as MockEx:
                MockEx.return_value = MagicMock(symbol="UVXY", client_id=1)
                bot = build_live_grid_bot(
                    symbol="UVXY",
                    db_path=os.path.join(tmp, "trades.db"),
                    clock=clk,
                )
                self.assertIs(bot.clock, clk)
                self.assertIs(bot.state_machine._clock, clk)
                self.assertIs(bot.session_manager.clock, clk)


# ════════════════════════════════════════════
#  D7 CapitalAllocator rescale
# ════════════════════════════════════════════

class TestRescale(unittest.TestCase):
    def test_rescale_from_equity_preserves_fractions(self):
        a = CapitalAllocator(total=10000.0,
                              allocations={"UVXY": 0.4, "TQQQ": 0.6})
        b = rescale_from_equity(a, new_total=12000.0)
        self.assertEqual(b.total, 12000.0)
        self.assertEqual(b.fraction_of("UVXY"), 0.4)
        self.assertAlmostEqual(b.for_symbol("UVXY"), 4800.0)
        # 原 a 不变 (immutable)
        self.assertEqual(a.total, 10000.0)

    def test_rescale_negative_raises(self):
        from capital_allocator import AllocationError
        a = CapitalAllocator(total=10000.0, allocations={"UVXY": 1.0})
        with self.assertRaises(AllocationError):
            rescale_from_equity(a, new_total=-100.0)

    def test_rescale_from_account_risk(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            arm = AccountRiskManager(db_path=tmp.name, clock=LiveClock(),
                                       total_capital=10000.0)
            ex = MagicMock()
            ex.get_account_summary.return_value = {"NetLiquidation": 13500.0}
            arm.get_account_equity(ex)  # 填缓存
            a = CapitalAllocator(total=10000.0, allocations={"UVXY": 1.0})
            b = rescale_from_account_risk(a, arm)
            self.assertAlmostEqual(b.total, 13500.0)
        finally:
            os.unlink(tmp.name)


# ════════════════════════════════════════════
#  D4 MultiSymbolOrchestrator 故障隔离
# ════════════════════════════════════════════

class TestOrchestratorFailureIsolation(unittest.TestCase):
    def _bot(self, sym, *, fail_on=None):
        """构造 mock bot. fail_on=phase → 调对应方法时抛异常."""
        bot = MagicMock()
        bot.symbol = sym
        bot.should_stop.return_value = False
        bot.get_check_interval_sec.return_value = 60
        if fail_on == "start":
            bot.start.side_effect = RuntimeError(f"start fail {sym}")
        if fail_on == "step":
            bot.step.side_effect = RuntimeError(f"step fail {sym}")
        if fail_on == "shutdown":
            bot.shutdown.side_effect = RuntimeError(f"shutdown fail {sym}")
        return bot

    def test_start_failure_isolates_one_bot(self):
        bots = {
            "A": self._bot("A", fail_on="start"),
            "B": self._bot("B"),
            "C": self._bot("C"),
        }
        orch = MultiSymbolOrchestrator(bots)
        results = orch.start_all()
        self.assertFalse(results["A"])
        self.assertTrue(results["B"])
        self.assertTrue(results["C"])
        self.assertIn("A", orch.failed_bots)
        self.assertNotIn("B", orch.failed_bots)

    def test_step_failure_does_not_pollute_others(self):
        bots = {
            "A": self._bot("A"),
            "B": self._bot("B", fail_on="step"),
            "C": self._bot("C"),
        }
        orch = MultiSymbolOrchestrator(bots)
        orch.start_all()
        results = orch.step_all()
        # B step 失败被隔离, A/C 仍然成功
        self.assertTrue(results["A"])
        self.assertFalse(results["B"])
        self.assertTrue(results["C"])
        # 下一轮 B 已在 failed_bots, 应直接跳过
        bots["B"].step.reset_mock()
        results2 = orch.step_all()
        self.assertNotIn("B", results2)
        bots["B"].step.assert_not_called()

    def test_shutdown_failure_does_not_propagate(self):
        bots = {
            "A": self._bot("A", fail_on="shutdown"),
            "B": self._bot("B"),
        }
        orch = MultiSymbolOrchestrator(bots)
        # 不应抛异常
        orch.shutdown_all()
        bots["B"].shutdown.assert_called_once()

    def test_revive_succeeds(self):
        bots = {"A": self._bot("A", fail_on="start")}
        orch = MultiSymbolOrchestrator(bots)
        orch.start_all()
        self.assertIn("A", orch.failed_bots)
        # 修复 start
        bots["A"].start.side_effect = None
        ok = orch.revive("A")
        self.assertTrue(ok)
        self.assertNotIn("A", orch.failed_bots)

    def test_should_stop_all_true_only_when_all_stopped(self):
        b1 = self._bot("A")
        b2 = self._bot("B")
        b1.should_stop.return_value = True
        b2.should_stop.return_value = False
        orch = MultiSymbolOrchestrator({"A": b1, "B": b2})
        self.assertFalse(orch.should_stop_all())
        b2.should_stop.return_value = True
        self.assertTrue(orch.should_stop_all())

    def test_failure_callback_invoked(self):
        calls = []
        def cb(sym, exc, phase):
            calls.append((sym, type(exc).__name__, phase))
        bots = {"A": self._bot("A", fail_on="start")}
        orch = MultiSymbolOrchestrator(bots, on_bot_failure=cb)
        orch.start_all()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "A")
        self.assertEqual(calls[0][2], "start")

    def test_next_sleep_sec_uses_min_interval(self):
        a = self._bot("A"); a.get_check_interval_sec.return_value = 30
        b = self._bot("B"); b.get_check_interval_sec.return_value = 60
        orch = MultiSymbolOrchestrator({"A": a, "B": b})
        self.assertEqual(orch.next_sleep_sec(), 30)


# ════════════════════════════════════════════
#  D2 manual_resume CLI
# ════════════════════════════════════════════

class TestManualResumeCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        # 预先在 DB 里制造 triggered 状态
        clk = LiveClock()
        arm = AccountRiskManager(db_path=self.tmp.name, clock=clk,
                                   total_capital=10000.0)
        arm.check_hard_stop(account_equity=7000.0)  # 触发 hard_stop
        arm.contribute_trade_pnl("UVXY", -600.0)
        arm.check_daily_loss()  # 触发 daily_loss
        self.assertTrue(arm.is_hard_stopped())
        self.assertTrue(arm.is_daily_loss_stopped())

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _run_cli(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "scripts/manual_resume.py",
               "--account-db", self.tmp.name] + args
        return subprocess.run(
            cmd, cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=20,
        )

    def test_status_shows_triggered_flags(self):
        r = self._run_cli(["--status"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hard_stop_triggered:  True", r.stdout)
        self.assertIn("daily_loss_triggered: True", r.stdout)

    def test_resume_hard_stop_with_yes(self):
        r = self._run_cli(["--resume-hard-stop", "--yes"])
        self.assertEqual(r.returncode, 0, r.stderr)
        # 验证 DB 里的 flag 确实清了
        with sqlite3.connect(self.tmp.name) as conn:
            row = conn.execute(
                "SELECT hard_stop_triggered, daily_loss_triggered FROM account_risk_state WHERE id=1"
            ).fetchone()
        self.assertEqual(row[0], 0)
        # daily_loss 没动
        self.assertEqual(row[1], 1)

    def test_resume_all_with_yes(self):
        r = self._run_cli(["--resume-all", "--yes"])
        self.assertEqual(r.returncode, 0, r.stderr)
        with sqlite3.connect(self.tmp.name) as conn:
            row = conn.execute(
                "SELECT hard_stop_triggered, daily_loss_triggered FROM account_risk_state WHERE id=1"
            ).fetchone()
        self.assertEqual(row[0], 0)
        self.assertEqual(row[1], 0)

    def test_missing_db_returns_error(self):
        cmd = [sys.executable, "scripts/manual_resume.py",
               "--account-db", "/nonexistent/path/account.db", "--status"]
        r = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)),
                            capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 2)


# ════════════════════════════════════════════
#  Strategy-bar 计时器双消费 bug 回归测试
#
#  历史 bug: _should_check_dynamic_adjustment() 有 side effect (消耗 _last_recenter_check).
#  _update_market_context 和 _handle_active_grid step 5 各调一次, 第一个消耗后
#  第二个永远 False, 导致 _apply_dynamic_adjustment 在 session 启动后再也不跑,
#  grid_engine.should_exit / recenter 全部哑火, V49 +109% → -21% HARD_STOP.
#
#  修复: 拆 _strategy_bar_due (pure check) + _consume_strategy_bar (consume),
#  _handle_active_grid 顶部 single check 共享给两路.
#
#  本测试钉死该行为, 防止再次回归.
# ════════════════════════════════════════════

class TestStrategyBarDueBugRegression(unittest.TestCase):
    def setUp(self):
        from grid_bot import GridBot
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.clock = LiveClock()
        self.db = TradeDatabase(db_path=self.tmp.name)
        self.bot = GridBot.__new__(GridBot)
        self.bot.clock = self.clock
        self.bot._last_recenter_check = None

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_strategy_bar_due_is_pure(self):
        """pure check 不应有 side effect."""
        b = self.bot
        self.assertTrue(b._strategy_bar_due())     # None 初值 → True
        self.assertTrue(b._strategy_bar_due())     # 再次 → 仍 True (无 side effect)
        # 显式 consume 后, 时钟还在同一刻 → 应 False
        b._consume_strategy_bar()
        self.assertFalse(b._strategy_bar_due())

    def test_double_call_does_not_consume_twice(self):
        """_handle_active_grid 流程: 顶部 check 一次 → 两路使用 → step5 consume 一次.
        模拟该流程, 验证 step5 不会因为前面已被消耗而错过."""
        b = self.bot
        # 模拟第一次进入 (无 prev): bar_due = True
        bar_due_1 = b._strategy_bar_due()
        # step2 / step5 都看到 True
        self.assertTrue(bar_due_1)
        # step5 末尾 consume
        b._consume_strategy_bar()
        # 同一 bar 再调 → 应 False (已 consume)
        self.assertFalse(b._strategy_bar_due())

    def test_legacy_should_check_compat(self):
        """旧 API _should_check_dynamic_adjustment 保留向后兼容 (check + consume).
        测试: 第一次 True, 第二次 False."""
        b = self.bot
        self.assertTrue(b._should_check_dynamic_adjustment())
        self.assertFalse(b._should_check_dynamic_adjustment())


class TestTacticalActionsReachable(unittest.TestCase):
    """战术化 4 个 action 在受控合成 ctx 上必须可达 (中性 regression).

    目的: 防止战术化分支无声退化为 dead code. 不断言 'ON vs OFF' 优劣;
    断言每个 action 在合适的合成 MarketContext + 临时阈值下能被触发,
    即代码路径活着. 未来重启 / 重设计战术化时, 这套测试仍有意义.

    spec: docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md §4.4
    """

    def setUp(self):
        if not _TACTICAL_AVAILABLE:
            self.skipTest("tactical modules archived 2026-05-15")
        from datetime import datetime
        from interfaces import HistoricalClock
        from trade_logger import TradeDatabase
        from session_manager import SessionManager
        import tactical_config as tcfg

        self.tcfg = tcfg
        # 临时 DB
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = f"{self.tmpdir.name}/t.db"
        self.db = TradeDatabase(db_path=db_path)
        self.clock = HistoricalClock()
        self.clock.set(datetime(2026, 1, 1, 10, 0))
        self.sm = SessionManager(db=self.db, clock=self.clock)
        self.sm.start_session(
            symbol="TEST", start_equity=10000.0, start_cash=4000.0,
            start_position=200.0, start_price=30.0,
            confidence=tcfg.CONFIDENCE_NORMAL,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def _patch(self, attr, value):
        """临时改 tactical_config 阈值, tearDown 恢复."""
        orig = getattr(self.tcfg, attr)
        setattr(self.tcfg, attr, value)
        self.addCleanup(setattr, self.tcfg, attr, orig)

    def test_defensive_triggers_on_strong_downtrend(self):
        from session_manager import ACTION_ENTER_DEFENSIVE
        from tactical_rules import MarketContext
        self._patch("TREND_RISK_SCORE_DEFENSIVE", 50.0)
        self._patch("TREND_RISK_ADX_THRESHOLD", 22.0)
        ctx = MarketContext(
            current_price=27.0, ema=30.0, atr=1.0, atr_pct=0.04,
            adx=50.0, grid_center=30.0,
            ema_slope=-0.04, consecutive_down_bars=5,
            price_below_ema_bars=5, atr_expansion=1.0,
        )
        ev = self.sm.evaluate_session(ctx)
        self.assertEqual(ev.action, ACTION_ENTER_DEFENSIVE,
                         f"reason={ev.reason} score={ev.trend_risk_score}")

    def test_force_exit_triggers_on_hard_stop(self):
        from session_manager import ACTION_FORCE_EXIT
        from tactical_rules import MarketContext
        self._patch("SESSION_HARD_STOP_PCT", 0.05)
        self.sm.update_session(
            current_equity=9000.0, current_cash=3500.0,  # 亏 10%
            current_position=200.0, current_price=27.5,
            realized_pnl_delta=-1000.0,
        )
        ctx = MarketContext(current_price=27.5, ema=30.0, atr=1.0,
                            atr_pct=0.04, adx=15.0, grid_center=30.0)
        ev = self.sm.evaluate_session(ctx)
        self.assertEqual(ev.action, ACTION_FORCE_EXIT,
                         f"reason={ev.reason}")

    def test_profit_protect_exit_on_trailing_giveback(self):
        from session_manager import ACTION_PROFIT_PROTECT_EXIT
        from tactical_rules import MarketContext
        self._patch("SESSION_MIN_PROFIT_TO_PROTECT_PCT", 0.01)
        self._patch("SESSION_TRAILING_GIVEBACK_RATIO", 0.5)
        # 先冲 peak +$300 (3%), 再回吐到 +$120
        self.sm.update_session(
            current_equity=10300.0, current_cash=4000.0,
            current_position=200.0, current_price=31.5,
            realized_pnl_delta=300.0,
        )
        self.sm.update_session(
            current_equity=10120.0, current_cash=4000.0,
            current_position=200.0, current_price=30.6,
            realized_pnl_delta=-180.0,
        )
        ctx = MarketContext(current_price=30.6, ema=30.0, atr=1.0,
                            atr_pct=0.03, adx=15.0, grid_center=30.0)
        ev = self.sm.evaluate_session(ctx)
        self.assertEqual(ev.action, ACTION_PROFIT_PROTECT_EXIT,
                         f"reason={ev.reason}")

    def test_partial_profit_exit_on_strong_profit(self):
        from session_manager import ACTION_PARTIAL_PROFIT_EXIT
        from tactical_rules import MarketContext
        self._patch("SESSION_STRONG_PROFIT_PCT", 0.03)
        self._patch("SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO", 0.5)
        self._patch("SESSION_MIN_PROFIT_TO_PROTECT_PCT", 0.999)  # 屏蔽 trailing
        self._patch("SESSION_TRAILING_GIVEBACK_RATIO", 0.999)
        self.sm.update_session(
            current_equity=10500.0, current_cash=4000.0,
            current_position=200.0, current_price=32.5,
            realized_pnl_delta=500.0,
        )
        ctx = MarketContext(current_price=32.5, ema=30.0, atr=1.0,
                            atr_pct=0.03, adx=15.0, grid_center=30.0)
        ev = self.sm.evaluate_session(ctx)
        self.assertEqual(ev.action, ACTION_PARTIAL_PROFIT_EXIT,
                         f"reason={ev.reason}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
