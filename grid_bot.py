"""
grid_bot.py — 网格交易核心逻辑

这个模块不区分实盘和回测 — 所有时间和执行动作都通过 clock 和 executor 两个
注入的抽象接口完成:

  实盘入口: main.py    → LiveClock + IBKRExecutor
  回测入口: backtest.py → HistoricalClock + SimulatedExecutor

在这里维护的一套交易逻辑 = 实盘跑的逻辑 = 回测跑的逻辑, 不存在镜像。
"""

import logging
import os
import time
from datetime import datetime, date, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import config
import indicators
from entry_filter import EntryFilter
from grid_engine import DynamicGridEngine
from interfaces import Clock, Executor
from pnl_tracker import PnLTracker
from risk_manager import RiskManager
from state_machine import (
    StateMachine, SystemState, is_grid_state, is_position_holding_state,
)
from trade_logger import TradeDatabase

ET = ZoneInfo("America/New_York")
logger = logging.getLogger("GridTrader.Bot")


def _grid_state_path(db_path: str) -> str:
    """网格引擎 JSON 快照路径, 与 SQLite 数据库同目录."""
    return f"{db_path}.grid.json"


def _base_shares_path(db_path: str) -> str:
    return f"{db_path}.base_shares.txt"


class GridBot:
    """
    网格交易机器人 (逻辑核心)
    
    使用方式:
      # 实盘:
      bot = GridBot(clock=LiveClock(), executor=IBKRExecutor(),
                    db=TradeDatabase(), pnl=PnLTracker(), ...,
                    data_fetcher=DataProvider())
      bot.start()
      while not bot.should_stop():
          bot.step()
      bot.shutdown()
      
      # 回测:
      clock = HistoricalClock()
      executor = SimulatedExecutor(df, capital, clock)
      bot = GridBot(clock=clock, executor=executor, ..., 
                    data_fetcher=HistoricalDataFetcher(df, clock))
      bot.start()
      for i, ts in enumerate(df.index):
          clock.set(ts)
          executor.set_bar_index(i)
          bot.step()
      bot.shutdown()
    """

    def __init__(self,
                 clock: Clock,
                 executor: Executor,
                 db: TradeDatabase,
                 pnl: PnLTracker,
                 risk: RiskManager,
                 state_machine: StateMachine,
                 entry_filter: EntryFilter,
                 data_fetcher,
                 strategy_df_days: int = None,
                 # 2026-05-15 Phase 4.E: 多标的支持. 单标的不传, fallback config.TOTAL_CAPITAL.
                 allocated_capital: float = None,
                 symbol: str = None,
                 capital_provider=None):
        self.clock = clock
        self.executor = executor
        self.db = db
        self.pnl = pnl
        self.risk = risk
        self.state_machine = state_machine
        self.entry_filter = entry_filter
        self.data_fetcher = data_fetcher  # 需要有 get_strategy_data(symbol, days)
        self.strategy_df_days = strategy_df_days or config.HISTORY_LOOKBACK_DAYS
        self._allocated_capital = allocated_capital
        self._symbol = symbol or getattr(executor, 'symbol', None) or config.SYMBOL
        self._capital_provider = capital_provider  # 现阶段未实际使用, 仅 signature 兼容

        self.grid: Optional[DynamicGridEngine] = None
        self._running = False
        self._last_scan_time: Optional[datetime] = None
        self._last_recenter_check: Optional[datetime] = None
        self._last_weekly_report_week: Optional[tuple] = None  # (iso_year, iso_week)
        # daily_snapshots 防重复 — 当天写过一次后, _maybe_log_daily_snapshot
        # 不再重复触发. 关键事件 (_log_daily_snapshot_now) 仍然会覆盖当天记录.
        self._last_snapshot_date: Optional[date] = None
        self._base_position_shares = 0.0
        # 连续 _execute_entry 失败计数; 达到 config.ENTRY_EXECUTION_MAX_FAILURES
        # 时自动回 SCANNING, 避免 waiting_entry 长期卡死.
        self._entry_execution_failures = 0

    @property
    def symbol(self) -> str:
        return self._symbol

    def _capital(self) -> float:
        """返回本 bot 的资金参考. 多标的传 allocated_capital, 单标的 fallback TOTAL_CAPITAL.

        2026-05-15 Phase 4.E: 加入此 helper 避免 sub-bot 直接读 config.TOTAL_CAPITAL
        (那是全账户值, 多标的会 oversubscribe).

        getattr 安全访问: __new__-style 测试夹具不走 __init__, 故 _allocated_capital
        可能未设置; 用 getattr(..., None) 而非裸属性访问, 保证测试不崩溃.
        """
        allocated = getattr(self, "_allocated_capital", None)
        if allocated is not None:
            return float(allocated)
        return float(config.require_total_capital())

    # ───────────────────────────────
    #  生命周期
    # ───────────────────────────────

    def start(self):
        """
        启动: 连接 + 恢复状态 + (实盘) 与 broker 对账
        支持 executor 已被外部预连接.

        恢复顺序 (必须是这个序):
          1. PnL FIFO 队列 (pnl.load_state — 从 sqlite)
          2. StateMachine  (state_machine.load_state — 从 sqlite)
          3. DynamicGridEngine (从 JSON 快照, 仅当状态允许网格存在)
          4. _base_position_shares (从磁盘)
          5. risk._prev_close (从 sqlite)
          6. (实盘) reconcile_with_broker — 拉 IBKR 真实持仓/挂单并校验
        """
        if not self.executor.is_connected():
            if not self.executor.connect():
                logger.error("Executor 连接失败")
                raise RuntimeError("Executor 连接失败")

        self.pnl.load_state()
        qs = self.pnl.get_queue_summary()
        if qs["count"] > 0:
            logger.info(f"📂 PnL队列恢复: {qs['count']}笔 均价${qs['avg_price']:.2f}")

        # StateMachine 恢复
        try:
            self.state_machine.load_state(self.db.db_path)
        except Exception as e:
            logger.error(f"StateMachine 恢复失败: {e}")

        # GridEngine 恢复 (仅当之前处于 grid states / EXIT_PENDING)
        self._try_restore_grid()

        # _base_position_shares 恢复
        self._restore_base_shares()

        startup_price = self.executor.get_current_price()
        broker_prev_close = None
        try:
            broker_prev_close = self.executor.get_prev_close()
        except Exception as e:
            logger.warning(f"取 broker 历史 prev_close 失败 (非致命): {e}")
        self.risk.initialize_from_db(
            current_price=startup_price,
            broker_prev_close=broker_prev_close,
        )

        # 实盘 broker reconcile (回测 executor 没有这个能力, 会跳过)
        if hasattr(self.executor, "reconcile_on_startup"):
            try:
                self._reconcile_with_broker()
            except Exception as e:
                logger.error(f"broker 对账失败 (非致命, 继续运行): {e}", exc_info=True)

        self._running = True
        logger.info(f"🚀 进入主循环 | 状态={self.state_machine.state.value}")

    def _try_restore_grid(self):
        """根据状态机当前状态恢复 DynamicGridEngine.

        两条独立分支:
          * EXIT_PENDING — grid 快照可选: _handle_exit_pending 只用持仓+FIFO,
            缺 .grid.json 不是错误, 保持 EXIT_PENDING 继续清仓.
            (旧实现把它和 grid states 合并, 缺快照就回退 SCANNING,
             再靠 reconcile drift #1 绕回 EXIT_PENDING — 状态写两次, 日志噪声.)
          * grid states (OFFENSIVE/DEFENSIVE/ACTIVE) — grid 快照必需:
            缺/损坏均回退 SCANNING, 由后续 reconcile 决定如何修正持仓.
          * 其它状态 — 不动.
        """
        state = self.state_machine.state
        path = _grid_state_path(self.db.db_path)

        if state == SystemState.EXIT_PENDING:
            if not os.path.exists(path):
                logger.info(
                    f"EXIT_PENDING 启动: 无网格快照 {path}, 仅按 FIFO+持仓清仓"
                )
                return
            try:
                self.grid = DynamicGridEngine.load_state(path)
                logger.info(
                    f"📂 网格引擎恢复 (EXIT_PENDING) | "
                    f"中轴${self.grid.center_price:.2f}"
                )
            except Exception as e:
                logger.warning(
                    f"EXIT_PENDING 启动: 网格快照损坏 {path}: {e}, "
                    f"仅按 FIFO+持仓清仓"
                )
                self.grid = None
            return

        if not is_grid_state(state):
            return

        if not os.path.exists(path):
            logger.warning(
                f"状态={state.value} 但未找到网格快照 {path}, 回退到 SCANNING"
            )
            self.state_machine.transition_to(
                SystemState.SCANNING, "重启时网格快照缺失",
                now=self.clock.now()
            )
            self.state_machine.save_state(self.db.db_path)
            return
        try:
            self.grid = DynamicGridEngine.load_state(path)
            logger.info(
                f"📂 网格引擎恢复 | 中轴${self.grid.center_price:.2f} "
                f"档距{self.grid.spacing_pct*100:.2f}% "
                f"已成交买{self.grid.total_filled_buys} 卖{self.grid.total_filled_sells}"
            )
        except Exception as e:
            logger.error(f"网格快照恢复失败 {path}: {e}, 回退 SCANNING")
            self.state_machine.transition_to(
                SystemState.SCANNING, f"网格快照损坏: {e}",
                now=self.clock.now()
            )
            self.state_machine.save_state(self.db.db_path)

    def _restore_base_shares(self):
        path = _base_shares_path(self.db.db_path)
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                self._base_position_shares = float(f.read().strip() or "0")
            if self._base_position_shares > 0:
                logger.info(f"📂 底仓股数恢复: {self._base_position_shares:.4f}")
        except Exception as e:
            logger.warning(f"底仓股数恢复失败 {path}: {e}")

    # ───────────────────────────────
    #  持久化辅助
    # ───────────────────────────────

    def _persist_all(self):
        """把 state_machine + grid + base_shares 一起落盘 (在重要事件后调用)."""
        try:
            self.state_machine.save_state(self.db.db_path)
        except Exception as e:
            logger.error(f"StateMachine 落盘失败: {e}")
        try:
            if self.grid:
                self.grid.save_state(_grid_state_path(self.db.db_path))
            else:
                # 无网格: 清理旧快照避免下次误恢复
                path = _grid_state_path(self.db.db_path)
                if os.path.exists(path):
                    os.remove(path)
        except Exception as e:
            logger.error(f"GridEngine 落盘失败: {e}")
        try:
            with open(_base_shares_path(self.db.db_path), "w") as f:
                f.write(f"{self._base_position_shares:.4f}")
        except Exception as e:
            logger.error(f"底仓股数落盘失败: {e}")

    def _fetch_reconcile_with_retry(
        self,
        max_attempts: int = 3,
        backoff_sec: tuple = (2.0, 5.0, 10.0),
    ) -> dict:
        """对 `executor.reconcile_on_startup()` 加 IBKR Gateway boot-window 防护.

        问题: ib-gateway 的 healthcheck (4004 监听通过) 比 reqAllOpenOrders
        完全返回开放订单列表要早. 如果 reconcile 跑在这个空窗里, broker 端会
        假报"空仓 + 零开放单", 触发本函数下游的 drift #2 → 误清本地 FIFO →
        实盘风险.

        策略: 仅当 local state 处于持仓状态 (is_position_holding_state)
        且 broker 报空仓 (real_shares < 1e-4) 时, 才视为可疑并重试.
        其它情况立即接受首次结果, 不引入额外延迟.

        Returns: 最后一次 reconcile 的 dict {position_shares, open_orders}.
        """
        state = self.state_machine.state
        local_expects_positions = is_position_holding_state(state)

        for attempt in range(1, max_attempts + 1):
            reconcile = self.executor.reconcile_on_startup()
            real_shares = float(reconcile.get("position_shares", 0.0))
            broker_empty = real_shares < 0.0001

            # 立即接受当: 不预期持仓 (无 race 可能) 或 broker 报有持仓.
            if not local_expects_positions or not broker_empty:
                return reconcile

            # 走到这里: local 预期持仓 + broker 空仓 = 可疑 race.
            if attempt < max_attempts:
                wait = backoff_sec[min(attempt - 1, len(backoff_sec) - 1)]
                logger.info(
                    f"reconcile attempt {attempt}/{max_attempts}: broker 报空仓 "
                    f"但 local state={state.value}; {wait}s 后重试 "
                    f"(Gateway boot-window 防护)"
                )
                time.sleep(wait)
            else:
                logger.warning(
                    f"reconcile: {max_attempts} 次尝试后 broker 仍报空仓 "
                    f"(local state={state.value}); 接受 broker 空仓口径, "
                    f"drift #2 将清 FIFO + 回 SCANNING"
                )
        return reconcile

    def _reconcile_with_broker(self):
        """
        启动时与 IBKR 真实状态对账:
          - 拉真实持仓 (position shares)
          - 拉真实开放订单 (接管 orderId, 与本地 grid.levels 对应)
          - 校验 state_machine 口径是否与持仓/挂单一致
          - 出现严重漂移时 (持仓有但本地记为空仓) 直接进入 EXIT_PENDING 人工确认
        """
        logger.info("🔄 开始与 IBKR 对账...")
        reconcile = self._fetch_reconcile_with_retry()  # B2: boot-window race protection
        real_shares = float(reconcile.get("position_shares", 0.0))
        open_orders = reconcile.get("open_orders", [])
        logger.info(
            f"   IBKR 真实: 持仓 {real_shares:.4f} 股 | 开放订单 {len(open_orders)} 个"
        )

        state = self.state_machine.state
        fifo_qty = float(self.pnl.get_queue_summary()["total_qty"])
        local_total_expected = fifo_qty + float(self._base_position_shares)

        # ── 漂移 #1: IBKR 有仓位但本地认为空仓 ──
        if real_shares > 0.0001 and state == SystemState.SCANNING:
            self.db.log_risk_event(
                "RECONCILE_DRIFT",
                f"IBKR持仓{real_shares:.4f}股 但本地 state=SCANNING",
                "切入 EXIT_PENDING, 保护人工介入"
            )
            logger.critical(
                "🚨 漂移: 账户有仓位但本地 SCANNING → 强制 EXIT_PENDING"
            )
            self.state_machine.transition_to(
                SystemState.EXIT_PENDING, "reconcile: broker 有仓位",
                now=self.clock.now()
            )

        # ── 漂移 #2: 本地认为有仓但 IBKR 空仓 ──
        if real_shares < 0.0001 and (is_grid_state(state) or state == SystemState.EXIT_PENDING):
            self.db.log_risk_event(
                "RECONCILE_DRIFT",
                f"IBKR 空仓但本地 state={state.value} 含 FIFO {fifo_qty:.4f}",
                "清 FIFO + 回 SCANNING"
            )
            logger.critical(
                "🚨 漂移: 本地有仓状态但 IBKR 空仓 → 清 FIFO 回 SCANNING"
            )
            if self.pnl.buy_queue:
                self.pnl.buy_queue.clear()
                self.pnl.save_state()
            self._base_position_shares = 0.0
            self.grid = None
            self.state_machine.transition_to(
                SystemState.SCANNING, "reconcile: broker 空仓",
                now=self.clock.now()
            )

        # ── 漂移 #3: 数量不一致 (允许 1e-3 容差) ──
        if real_shares > 0.0001 and abs(real_shares - local_total_expected) > 1e-3:
            self.db.log_risk_event(
                "RECONCILE_QTY_MISMATCH",
                f"IBKR {real_shares:.4f} vs local {local_total_expected:.4f} "
                f"(FIFO {fifo_qty:.4f} + base {self._base_position_shares:.4f})",
                "已记录, 按 IBKR 真实值继续"
            )
            logger.warning(
                f"⚠️ 持仓数量漂移: broker {real_shares:.4f} 本地 "
                f"{local_total_expected:.4f}, 以 broker 为准"
            )

        # ── 开放订单接管 ──
        if open_orders:
            self._reconcile_open_orders(open_orders)

        self._persist_all()
        logger.info("✓ 对账完成")

    def _reconcile_open_orders(self, open_orders: list):
        """
        把 IBKR 端的开放订单注册进 executor.active_orders + grid.levels.

        策略:
          - 如果有网格: 按 (side, price) 近似匹配 grid level_index
          - 否则: 全部作为孤儿单撤销, 防止误成交
        """
        if not self.grid:
            logger.warning(f"无本地网格 — 撤销 {len(open_orders)} 个孤儿订单")
            for o in open_orders:
                self.executor.cancel_order(o["order_id"])
            return

        matched = 0
        orphans = []
        for o in open_orders:
            level_idx = self._match_order_to_level(o)
            if level_idx is None:
                orphans.append(o)
                continue
            # 注入 executor.active_orders 供 check_order_fills 识别
            self.executor.adopt_order(
                order_id=o["order_id"],
                trade=o["trade"],
                action=o["action"],
                quantity=o["quantity"],
                price=o["price"],
                level_index=level_idx,
                order_type=o.get("order_type", "GRID_RECONCILED"),
            )
            self.grid.mark_order_placed(level_idx, o["order_id"])
            matched += 1

        logger.info(f"   接管 {matched} 订单, 撤销 {len(orphans)} 孤儿")
        for o in orphans:
            self.executor.cancel_order(o["order_id"])

        if orphans:
            self.db.log_risk_event(
                "RECONCILE_ORPHAN_ORDERS",
                f"{len(orphans)} 个开放订单无法匹配网格档位",
                "已撤销"
            )

    def _match_order_to_level(self, order: dict) -> Optional[int]:
        """按 (action, price) 匹配到 grid level. 容差 1 分钱."""
        if not self.grid:
            return None
        want_side = "SELL" if order["action"].upper() == "SELL" else "BUY"
        for idx, lv in self.grid.levels.items():
            lv_side = "SELL" if lv.side.value == "above" else "BUY"
            if lv_side != want_side:
                continue
            if abs(float(order["price"]) - lv.price) <= 0.01:
                return idx
        return None

    def shutdown(self):
        """关机: 持久化 + 断开 (GTC 订单保留在 IBKR, 不撤)"""
        try:
            self.pnl.save_state()
        except Exception as e:
            logger.error(f"保存PnL失败: {e}")
        try:
            self._persist_all()
        except Exception as e:
            logger.error(f"保存 bot 状态失败: {e}")
        self.executor.disconnect()
        logger.info("✅ 已退出 (状态已落盘, 下次启动可恢复)")

    def request_stop(self):
        self._running = False

    def should_stop(self) -> bool:
        return not self._running

    def get_check_interval_sec(self) -> int:
        """当前状态下的主循环 sleep 时间"""
        return self.state_machine.get_check_interval_sec()

    # ───────────────────────────────
    #  主步进
    # ───────────────────────────────

    def step(self):
        """
        单步执行 — 实盘/回测都调用这个方法
        
        实盘: 主程序循环调用 step(), 然后 sleep(get_check_interval_sec())
        回测: driver 按 bar 推进 clock, 每个 bar 调一次 step()
        """
        if not self.executor.is_connected():
            logger.warning("Executor 未连接, 尝试重连")
            if not self.executor.reconnect():
                return

        state = self.state_machine.state

        if state == SystemState.SCANNING:
            self._handle_scanning()
        elif state == SystemState.WAITING_ENTRY:
            self._handle_waiting_entry()
        elif is_grid_state(state):
            # 2026-05-15 Phase 4.E: 兼容 OFFENSIVE_GRID / DEFENSIVE_GRID / ACTIVE_GRID
            self._handle_active_grid()
        elif state == SystemState.EXIT_PENDING:
            self._handle_exit_pending()

        # 每个交易日首次循环写一次账户快照 (与周报解耦)
        self._maybe_log_daily_snapshot()
        self._maybe_generate_weekly_report()

    # ───────────────────────────────
    #  SCANNING
    # ───────────────────────────────

    def _handle_scanning(self):
        now = self.clock.now()
        if (self._last_scan_time and
            (now - self._last_scan_time).total_seconds() < config.scanning_interval_sec()):
            return

        if not self.risk.check_trading_hours():
            return

        self._last_scan_time = now

        try:
            strategy_df = self.data_fetcher.get_strategy_data(
                config.SYMBOL, days=self.strategy_df_days
            )
            if strategy_df is None or len(strategy_df) < 50:
                return
        except Exception as e:
            logger.error(f"获取数据失败: {e}")
            return

        evaluation = self.entry_filter.evaluate(strategy_df, evaluation_time=now)
        self.db.log_entry_evaluation(
            evaluation.current_price, evaluation.adx_value, evaluation.atr_pct,
            evaluation.bb_width_pct, evaluation.ema_value,
            evaluation.allow_entry, "; ".join(evaluation.rejection_reasons),
        )

        if evaluation.conditions_passed:
            old = self.state_machine.state.value
            self.state_machine.on_entry_evaluation(True, "条件满足", now=now)
            self.db.log_state_transition(old, self.state_machine.state.value,
                                          "条件满足")
            self._persist_all()

            # 立即给一次 WAITING_ENTRY 评估机会 — 否则上层 main.py 会按
            # waiting_interval_sec() 再 sleep 一段时间才进入 _handle_waiting_entry,
            # 错过 "刚发现信号时 price 就在 band 内" 的瞬时入场窗口.
            # 仅当 transition 真的成功切到 WAITING_ENTRY 时才调用 (防止意外递归).
            if self.state_machine.state == SystemState.WAITING_ENTRY:
                logger.info(
                    "scanning→waiting: 立即触发一次 WAITING_ENTRY 评估 (避免 sleep 4h 后才看 timing)"
                )
                self._handle_waiting_entry()

    # ───────────────────────────────
    #  WAITING_ENTRY
    # ───────────────────────────────

    def _handle_waiting_entry(self):
        """WAITING_ENTRY 主处理.

        处理顺序 (重要 — 修改前请先读 README/CLAUDE 中关于 ENTRY_MAX_WAIT_BARS 的设计取向):
          1. 不在交易时间 → 直接 return, 不消耗 WAITING_ENTRY 窗口, 不下单.
          2. 重新拉数据 + entry_filter.evaluate.
          3. conditions_passed=False → 回 SCANNING (reason="条件失效").
          4. timing_passed=True     → 调 _execute_entry; 成功清零失败计数, 失败累加并按
                                       ENTRY_EXECUTION_MAX_FAILURES 自动回 SCANNING.
          5. timing_passed=False    → 记录详细日志, 然后再做 check_entry_timeout;
                                       超时回 SCANNING (reason="等待超时"), 否则继续 WAITING.

        关键: timeout 检查放在最后, 保证每个 bar 至少有一次完整的
        entry/timing 评估机会, 避免因调度漂移让 WAITING_ENTRY 在第一次重新评估前就被消耗.
        """
        now = self.clock.now()

        # ① 不在交易时段: 不消耗 WAITING_ENTRY 窗口.
        # 关键: 仅 return 还不够 — entry_window_started_at 不动, 经过夜盘/周末后
        # elapsed_bars 仍按 wall-clock 累加, 下次回到交易时段时直接超时. 因此把
        # entry_window_started_at 推到 "now", 让 timeout 只统计交易时段累计耗时.
        # (副作用: 跨 24h 多次非交易段调用都会 reset, 持续重置到最后一次非交易
        #  评估的时间; 第一次进交易时段时 elapsed≈调度间隔, 不会立刻超时. 这是
        #  设计预期 — "非交易时段不消耗窗口".)
        if not self.risk.check_trading_hours():
            ctx = self.state_machine.context
            if ctx.entry_window_started_at:
                ctx.entry_window_started_at = now.isoformat()
                try:
                    self.state_machine.save_state(self.db.db_path)
                except Exception as e:
                    logger.warning(f"WAITING_ENTRY 非交易时段窗口推进持久化失败 (非致命): {e}")
            logger.debug("WAITING_ENTRY: 非交易时段, 跳过本次评估 (窗口已推进, 不消耗)")
            return

        # 计算 elapsed_bars 仅用于日志 (实际超时由 state_machine.check_entry_timeout 决定)
        started_iso = self.state_machine.context.entry_window_started_at
        elapsed_bars = None
        if started_iso:
            try:
                started = datetime.fromisoformat(started_iso)
                strategy_hours = config.strategy_interval_hours()
                elapsed_bars = (now - started).total_seconds() / (strategy_hours * 3600)
            except Exception:
                elapsed_bars = None
        eb_str = f"{elapsed_bars:.3f}" if elapsed_bars is not None else "n/a"
        logger.info(
            f"WAITING_ENTRY 处理 | state={self.state_machine.state.value} "
            f"| elapsed_bars={eb_str} / max={config.ENTRY_MAX_WAIT_BARS}"
        )

        # ② 重新评估
        try:
            strategy_df = self.data_fetcher.get_strategy_data(
                config.SYMBOL, days=self.strategy_df_days
            )
            evaluation = self.entry_filter.evaluate(strategy_df, evaluation_time=now)
        except Exception as e:
            logger.error(f"WAITING_ENTRY 评估失败: {e}", exc_info=True)
            return

        cond_pass = bool(getattr(evaluation, "conditions_passed", False))
        timing_pass = bool(getattr(evaluation, "timing_passed", False))
        price = float(getattr(evaluation, "current_price", 0.0) or 0.0)
        ema = float(getattr(evaluation, "ema_value", 0.0) or 0.0)
        atr_pct = float(getattr(evaluation, "atr_pct", 0.0) or 0.0)
        bb_width_pct = float(getattr(evaluation, "bb_width_pct", 0.0) or 0.0)
        adx = float(getattr(evaluation, "adx_value", 0.0) or 0.0)
        rejection_reasons = list(getattr(evaluation, "rejection_reasons", []) or [])

        logger.info(
            f"WAITING_ENTRY evaluation | conditions_passed={cond_pass} "
            f"| timing_passed={timing_pass} | price={price:.4f} ema={ema:.4f} "
            f"atr_pct={atr_pct*100:.3f}% bb_width_pct={bb_width_pct*100:.3f}% "
            f"adx={adx:.2f}"
        )

        # ③ 条件失效: 立刻回 SCANNING
        if not cond_pass:
            old = self.state_machine.state.value
            self.state_machine.on_entry_evaluation(
                False, "; ".join(rejection_reasons[:1]), now=now
            )
            self.db.log_state_transition(old, self.state_machine.state.value,
                                          "条件失效")
            logger.info(
                "WAITING_ENTRY → SCANNING (条件失效) | "
                f"reasons={rejection_reasons[:3]}"
            )
            self._entry_execution_failures = 0
            self._persist_all()
            return

        # ④ 时机满足: 执行入场
        if timing_pass:
            logger.info(
                f"WAITING_ENTRY 准备执行入场 | price={price:.4f} ema={ema:.4f} "
                f"atr_pct={atr_pct*100:.3f}%"
            )
            success = self._execute_entry(evaluation)
            if success:
                self._entry_execution_failures = 0
                return

            # 失败路径: 累加计数; 达到阈值就回 SCANNING.
            self._entry_execution_failures += 1
            threshold = max(1, int(getattr(config, "ENTRY_EXECUTION_MAX_FAILURES", 1)))
            logger.warning(
                f"_execute_entry 失败 {self._entry_execution_failures}/{threshold} "
                f"(state={self.state_machine.state.value})"
            )
            if self._entry_execution_failures >= threshold:
                old = self.state_machine.state.value
                reason = (
                    f"入场执行连续失败 {self._entry_execution_failures} 次, "
                    f"自动回到扫描"
                )
                self.state_machine.transition_to(
                    SystemState.SCANNING, reason, now=now
                )
                self.db.log_state_transition(
                    old, self.state_machine.state.value, reason
                )
                log_risk_event = getattr(self.db, "log_risk_event", None)
                if callable(log_risk_event):
                    try:
                        log_risk_event(
                            "WAITING_ENTRY_AUTO_RESET",
                            f"连续 {self._entry_execution_failures} 次入场执行失败",
                            "自动回 SCANNING, 等待下一轮筛选"
                        )
                    except Exception as e:
                        logger.warning(f"log_risk_event 失败 (非致命): {e}")
                self._entry_execution_failures = 0
                self._persist_all()
            return

        # ⑤ 条件满足但 timing 未到: 记录日志再判超时
        timing_reasons = [r for r in rejection_reasons if "EMA" in r or "K线" in r]
        if not timing_reasons:
            timing_reasons = rejection_reasons[-2:]
        logger.info(
            f"WAITING_ENTRY timing 未到 | price={price:.4f} ema={ema:.4f} "
            f"atr_pct={atr_pct*100:.3f}% bb_width_pct={bb_width_pct*100:.3f}% "
            f"adx={adx:.2f} | timing_reasons={timing_reasons}"
        )

        old = self.state_machine.state.value
        if self.state_machine.check_entry_timeout(current_time=now):
            self.db.log_state_transition(old, self.state_machine.state.value,
                                          "等待超时")
            logger.info(
                f"WAITING_ENTRY → SCANNING (等待超时) | elapsed_bars={eb_str} "
                f"max={config.ENTRY_MAX_WAIT_BARS}"
            )
            self._entry_execution_failures = 0
            self._persist_all()
            return

        logger.info(
            f"WAITING_ENTRY 继续等待 | elapsed_bars={eb_str} "
            f"max={config.ENTRY_MAX_WAIT_BARS}"
        )

    def _execute_entry(self, evaluation):
        """市价单建底仓, 偏差>1.5% 回滚"""
        current_price = self.executor.get_current_price()
        if not current_price:
            connected = bool(getattr(self.executor, "is_connected", lambda: True)())
            reason = "executor_disconnected" if not connected else "no_quote_after_retry"
            logger.error(
                f"_execute_entry: 取价失败 reason={reason} — 跳过本次入场, 不下单"
            )
            log_risk_event = getattr(self.db, "log_risk_event", None)
            if callable(log_risk_event):
                try:
                    log_risk_event(
                        "EXECUTE_ENTRY_PRICE_UNAVAILABLE",
                        f"reason={reason} 入场条件已满足但取价失败",
                        "跳过入场, 等待下个 bar 重试"
                    )
                except Exception as e:
                    logger.warning(f"log_risk_event 失败 (非致命): {e}")
            return False

        base_capital = self._capital() * config.BASE_POSITION_RATIO
        base_shares = config.round_quantity(base_capital / current_price)
        if base_shares <= 0:
            logger.error(f"底仓数量为 0 (资金${base_capital:.2f} 价${current_price:.2f}), "
                         f"USE_FRACTIONAL={config.USE_FRACTIONAL}, 放弃入场")
            return False

        order_id = self.executor.place_market_order(
            action="BUY", quantity=base_shares,
            order_type_label="BASE_BUY"
        )
        if not order_id:
            return False

        fill = self.executor.wait_for_order_fill(order_id, timeout_sec=30)
        if not fill:
            logger.warning("底仓未成交, 取消")
            progress = self.executor.get_order_progress(order_id) or {}
            self.executor.cancel_order(order_id)
            self.executor.wait_for_order_fill(order_id, timeout_sec=5)
            partial = float(progress.get("filled_qty", 0.0))
            if partial > 0.0001:
                self._rollback_partial_base_entry(partial, progress)
            return False

        actual_price = fill["fill_price"]
        deviation = abs(actual_price - current_price) / current_price
        if deviation > 0.005:
            logger.warning(f"⚠️ 底仓偏差 {deviation*100:.2f}%")
            self.db.log_risk_event(
                "ENTRY_PRICE_SLIP",
                f"参考${current_price:.2f} 成交${actual_price:.2f}",
                f"偏差{deviation*100:.2f}%"
            )
            if deviation > 0.015:
                logger.critical("偏差过大, 回滚底仓")
                rollback = {
                    "filled_qty": fill["quantity"],
                    "avg_fill_price": actual_price,
                    "commission": fill.get("commission", 0),
                }
                self._rollback_partial_base_entry(fill["quantity"], rollback)
                return False

        commission = fill.get("commission", 0) or max(
            config.IBKR_COMMISSION_MIN,
            fill["quantity"] * config.IBKR_COMMISSION_PER_SHARE,
        )
        self._base_position_shares = fill["quantity"]
        self.db.log_trade(
            "BUY", config.SYMBOL, fill["quantity"], actual_price,
            order_type="BASE_BUY", grid_level=0,
            commission=commission, pnl=0.0,
            note=f"底仓 市价 参考${current_price:.2f}"
        )

        self.grid = DynamicGridEngine(
            center_price=evaluation.suggested_center,
            atr=evaluation.suggested_atr,
            spacing_pct=evaluation.suggested_spacing_pct,
            grid_capital=self._capital() * config.GRID_CAPITAL_RATIO,
            current_time=self.clock.now(),
        )
        logger.info(self.grid.get_grid_summary())

        old = self.state_machine.state.value
        self.state_machine.on_grid_active(now=self.clock.now())
        self.db.log_state_transition(
            old, self.state_machine.state.value,
            f"建仓完成 中轴${evaluation.suggested_center:.2f}"
        )
        self._persist_all()
        self._log_daily_snapshot_now(reason="entry_complete")
        return True

    def _rollback_partial_base_entry(self, partial_qty: float, progress: dict):
        avg_fill = float(progress.get("avg_fill_price", 0.0))
        buy_com = float(progress.get("commission", 0.0))

        self.db.log_trade(
            "BUY", config.SYMBOL, partial_qty, avg_fill,
            order_type="BASE_BUY_PARTIAL", grid_level=0,
            commission=buy_com, pnl=0.0,
            note="底仓部分成交 准备回滚"
        )

        rb_qty = config.round_quantity(partial_qty)
        if rb_qty <= 0:
            logger.warning(f"回滚数量 {partial_qty:.4f} 取整后为 0, 跳过")
            return
        rb_id = self.executor.place_market_order(
            action="SELL", quantity=rb_qty,
            order_type_label="ENTRY_ROLLBACK"
        )
        if not rb_id:
            self.db.log_risk_event("ENTRY_ROLLBACK_FAIL",
                                    f"{partial_qty:.4f}股回滚失败",
                                    "需人工处理")
            return

        rb_fill = self.executor.wait_for_order_fill(rb_id, timeout_sec=30)
        if not rb_fill:
            self.db.log_risk_event("ENTRY_ROLLBACK_TIMEOUT",
                                    f"{partial_qty:.4f}股回滚超时",
                                    "需人工处理")
            return

        self.db.log_trade(
            "SELL", config.SYMBOL, rb_fill["quantity"], rb_fill["fill_price"],
            order_type="ENTRY_ROLLBACK",
            commission=rb_fill["commission"], pnl=0.0,
            note="底仓回滚"
        )

    # ───────────────────────────────
    #  grid states (OFFENSIVE / DEFENSIVE / ACTIVE)
    # ───────────────────────────────

    def _handle_active_grid(self):
        if not self.grid:
            self.state_machine.transition_to(SystemState.SCANNING,
                                              "状态异常",
                                              now=self.clock.now())
            return

        # 1. 检查成交
        filled = self.executor.check_order_fills()
        grid_state_dirty = False
        had_fill = False
        for f in filled:
            had_fill = True
            grid_state_dirty = True
            self.grid.mark_order_filled(
                f["level_index"], f["fill_price"],
                self.clock.now().isoformat()
            )
            commission = f.get("commission", 0) or max(
                config.IBKR_COMMISSION_MIN,
                f["quantity"] * config.IBKR_COMMISSION_PER_SHARE,
            )
            if f["action"] == "BUY":
                self.pnl.record_buy(
                    quantity=f["quantity"], price=f["fill_price"],
                    commission=commission,
                    level_index=f["level_index"],
                    order_id=f["order_id"],
                )
                pnl = 0.0
            else:  # SELL
                sell_result = self.pnl.record_sell(
                    quantity=f["quantity"], price=f["fill_price"],
                    commission=commission, level_index=f["level_index"],
                )
                pnl = sell_result.net_pnl if sell_result else 0.0
                if sell_result and sell_result.unmatched_quantity > 1e-6:
                    self.db.log_risk_event(
                        "FIFO_UNDERFLOW",
                        f"未配对{sell_result.unmatched_quantity:.4f}股",
                        "检查底仓保护逻辑"
                    )

            self.db.log_trade(
                f["action"], config.SYMBOL, f["quantity"], f["fill_price"],
                order_type=f["order_type"], grid_level=f["level_index"],
                commission=commission, pnl=pnl,
            )

        # 2. 获取当前价
        current_price = self.executor.get_current_price()
        if not current_price:
            return

        self.risk.record_intraday_price(current_price)
        self.grid.check_filled_resets(current_price)

        # 3. 风控
        pos = self.executor.get_position_details()
        equity = self.executor.get_account_summary().get(
            "NetLiquidation", self._capital()
        )
        risk_check = self.risk.can_trade(current_price, equity, pos["market_value"])

        if self.risk.is_hard_stopped():
            self._emergency_liquidate(pos)
            return

        # 4. 动态调整 (按策略周期)
        if self._should_check_dynamic_adjustment():
            self._check_dynamic_adjustment(current_price)

        # 5. 下单
        if risk_check and is_grid_state(self.state_machine.state) and self.grid:
            signals = self.grid.check_signals(current_price)
            # 关键: SELL 的可卖量以 FIFO 队列为权威
            # (而非 pos - _base_position_shares, 后者在状态漂移时可能多算)
            # 这样才能防止 FIFO 下溢, 保证不会误卖底仓
            fifo_available = float(self.pnl.get_queue_summary()["total_qty"])
            for sig in signals:
                if sig["action"] == "SELL":
                    if fifo_available < sig["quantity"]:
                        continue
                if sig["action"] == "BUY":
                    cash = self.executor.get_cash()
                    if cash < sig["price"] * sig["quantity"]:
                        continue

                oid = self.executor.place_limit_order(
                    action=sig["action"],
                    quantity=sig["quantity"],
                    price=sig["price"],
                    level_index=sig["level_index"],
                    order_type_label=sig["order_type"]
                )
                if oid:
                    self.grid.mark_order_placed(sig["level_index"], oid)
                    grid_state_dirty = True
                    # 预扣 FIFO 可卖量 (BUY 会增加, SELL 会减少)
                    # 这样同一 tick 的多个 SELL 信号不会都通过
                    if sig["action"] == "SELL":
                        fifo_available -= sig["quantity"]

        if grid_state_dirty:
            self._persist_all()

        # 任何成交后刷新当日 snapshot — 反映最新 position_shares / cash / unrealized
        if had_fill:
            self._log_daily_snapshot_now(reason="grid_fill")

    def _should_check_dynamic_adjustment(self) -> bool:
        """按策略周期 (4h) 检查, 不是每分钟"""
        now = self.clock.now()
        if not self._last_recenter_check:
            self._last_recenter_check = now
            return True
        strategy_hours = config.strategy_interval_hours()
        if (now - self._last_recenter_check).total_seconds() < strategy_hours * 3600:
            return False
        self._last_recenter_check = now
        return True

    def _check_dynamic_adjustment(self, current_price: float):
        try:
            strategy_df = self.data_fetcher.get_strategy_data(
                config.SYMBOL, days=self.strategy_df_days
            )
            df_ind = indicators.compute_all_indicators(
                strategy_df,
                ema_period=config.GRID_CENTER_EMA_PERIOD,
                atr_period=14, adx_period=14, bb_period=20,
            )
            latest = df_ind.iloc[-1]
        except Exception as e:
            logger.error(f"动态调整数据失败: {e}")
            return

        # 把 ATR% 传给风控
        self.risk.update_atr_context(float(latest["ATR_PCT"]))

        # 退出
        should_exit, reason = self.grid.should_exit(
            current_price, float(latest["ADX"]), float(latest["ATR_PCT"])
        )
        if should_exit:
            old = self.state_machine.state.value
            self.state_machine.on_exit_signal(True, reason, now=self.clock.now())
            self.db.log_state_transition(old, self.state_machine.state.value, reason)
            self._persist_all()
            return

        # 重置
        should_rc, rc_reason = self.grid.should_recenter(
            current_price, float(latest["EMA"]), float(latest["ATR"]),
            current_time=self.clock.now()
        )
        if should_rc:
            old_center = self.grid.center_price
            old_spacing = self.grid.spacing_pct
            info = self.grid.recenter(
                float(latest["EMA"]), float(latest["ATR"]),
                current_time=self.clock.now()
            )
            self.executor.batch_cancel_orders(info["orders_to_cancel"])
            self.db.log_grid_recenter(
                old_center, info["new_center"],
                old_spacing, info["new_spacing"],
                float(latest["ATR"]), rc_reason
            )
            self.state_machine.on_recenter(now=self.clock.now())
            self._persist_all()

    # ───────────────────────────────
    #  EXIT_PENDING
    # ───────────────────────────────

    def _handle_exit_pending(self):
        # B4: 盘前/盘后不发市价单 (实盘会挂到次日开盘, 与回测口径不一致)
        # 硬止损走 _emergency_liquidate, 不经此处, 仍可任意时间触发.
        if not self.risk.check_trading_hours():
            return
        self.executor.cancel_all_orders()
        self.executor.sleep(2)

        pos = self.executor.get_position_details()
        if pos["shares"] < 0.0001:
            self._finalize_exit()
            return

        # 以 FIFO 队列作为网格仓权威来源
        qs = self.pnl.get_queue_summary()
        grid_in_queue = float(qs["total_qty"])
        actual = float(pos["shares"])
        grid_to_sell = min(grid_in_queue, actual)
        base_to_sell = max(0.0, actual - grid_to_sell)

        logger.info(f"EXIT分解: 持仓{actual:.4f}股 = 网格{grid_to_sell:.4f} + 底仓{base_to_sell:.4f}")

        filled_grid = 0.0
        filled_base = 0.0
        if grid_to_sell > 0.0001:
            filled_grid = self._liquidate_grid_shares(grid_to_sell)
        if base_to_sell > 0.0001:
            filled_base = self._liquidate_base_shares(base_to_sell)

        # 用本地算术推断剩余, 不重读 ib.portfolio() — 其 positionEvent 在 fill 后
        # 通常滞后 100–500 ms, 立即重读会得到 stale 缓存. fill["quantity"] 来自
        # Trade.orderStatus 是权威值. 下一轮 sleep(2) 后 IBKR positionEvent 已经
        # 到位, 启动时的 _reconcile_with_broker drift #2 也会兜底.
        estimated_remaining = max(0.0, actual - filled_grid - filled_base)
        if estimated_remaining < 0.0001:
            self._finalize_exit()
            return

        logger.warning(
            f"EXIT 本轮已卖 grid={filled_grid:.4f} base={filled_base:.4f}, "
            f"估计剩余 {estimated_remaining:.4f} 股, 下轮 IBKR 对账确认"
        )
        self.db.log_risk_event("EXIT_INCOMPLETE",
                                f"剩余约 {estimated_remaining:.4f} 股",
                                "保持 EXIT_PENDING")

    def _liquidate_grid_shares(self, grid_shares: float) -> float:
        """卖 grid_shares 股网格仓. 返回实际成交股数 (0.0 表示未成交)."""
        qty = config.round_quantity(grid_shares)
        if qty <= 0:
            logger.info(f"网格清仓数量 {grid_shares:.4f} 取整后为 0, 跳过")
            return 0.0
        oid = self.executor.place_market_order(
            action="SELL", quantity=qty,
            order_type_label="EXIT_GRID"
        )
        if not oid:
            self.db.log_risk_event("EXIT_ORDER_FAIL",
                                    f"网格{grid_shares:.4f}失败", "需人工")
            return 0.0

        fill = self.executor.wait_for_order_fill(oid, timeout_sec=60)
        if not fill:
            self.db.log_risk_event("EXIT_TIMEOUT",
                                    f"网格{grid_shares:.4f}超时",
                                    "需核查队列与持仓")
            return 0.0

        result = self.pnl.record_sell(
            quantity=fill["quantity"], price=fill["fill_price"],
            commission=fill["commission"], level_index=999,
        )
        pnl = result.net_pnl if result else 0.0
        self.db.log_trade(
            "SELL", config.SYMBOL, fill["quantity"], fill["fill_price"],
            order_type="EXIT_GRID", commission=fill["commission"],
            pnl=pnl,
            note=f"{self.state_machine.context.exit_reason} | 真实成交"
        )
        if fill["quantity"] + 0.0001 < grid_shares:
            self.db.log_risk_event(
                "EXIT_PARTIAL",
                f"计划{grid_shares:.4f} 实际{fill['quantity']:.4f}",
                "继续处理剩余"
            )
        return float(fill["quantity"])

    def _liquidate_base_shares(self, requested_qty: float) -> float:
        """卖 requested_qty 股底仓. 返回实际成交股数 (0.0 表示未成交)."""
        qty = config.round_quantity(requested_qty)
        if qty <= 0:
            logger.info(f"底仓清仓数量 {requested_qty:.4f} 取整后为 0, 跳过")
            return 0.0
        oid = self.executor.place_market_order(
            action="SELL", quantity=qty,
            order_type_label="EXIT_BASE"
        )
        if not oid:
            self.db.log_risk_event("EXIT_ORDER_FAIL",
                                    f"底仓{requested_qty:.4f}失败", "需人工")
            return 0.0

        fill = self.executor.wait_for_order_fill(oid, timeout_sec=60)
        if not fill:
            self.db.log_risk_event("EXIT_TIMEOUT",
                                    f"底仓{requested_qty:.4f}超时", "需人工")
            return 0.0

        self.db.log_trade(
            "SELL", config.SYMBOL, fill["quantity"], fill["fill_price"],
            order_type="EXIT_BASE", commission=fill["commission"],
            pnl=0.0,  # 底仓PnL由账户summary反映
            note="EXIT底仓 真实成交"
        )
        if fill["quantity"] + 0.0001 < requested_qty:
            self.db.log_risk_event(
                "EXIT_PARTIAL",
                f"底仓计划{requested_qty:.4f} 实际{fill['quantity']:.4f}",
                "继续处理剩余"
            )
        return float(fill["quantity"])

    def _finalize_exit(self):
        self._base_position_shares = 0
        self.grid = None
        old = self.state_machine.state.value
        self.state_machine.on_exit_complete(now=self.clock.now())
        self.db.log_state_transition(old, self.state_machine.state.value,
                                      "清仓完成")
        self.pnl.save_state()
        self._persist_all()
        self._log_daily_snapshot_now(reason="exit_complete")

    # ───────────────────────────────
    #  紧急清仓
    # ───────────────────────────────

    def _emergency_liquidate(self, pos: dict):
        logger.critical("🚨 紧急清仓")
        self.executor.cancel_all_orders()
        self.executor.sleep(2)

        total = pos["shares"]
        if total <= 0.0001:
            self._emergency_clear_fifo(pos.get("market_value", 0))
            self.db.log_risk_event("EMERGENCY_LIQUIDATE", "已空仓", "硬止损")
            self._running = False
            return

        qs = self.pnl.get_queue_summary()
        grid_in_queue = float(qs["total_qty"])
        grid_shares = min(grid_in_queue, total)
        base_shares = max(0.0, total - grid_shares)

        emergency_qty = config.round_quantity(total)
        if emergency_qty <= 0:
            logger.critical(f"紧急清算数量 {total:.4f} 取整为 0, 强制转为 0 股")
            emergency_qty = 0.0
        oid = self.executor.place_market_order(
            action="SELL", quantity=emergency_qty,
            order_type_label="EMERGENCY"
        )
        if not oid:
            self.db.log_risk_event("EMERGENCY_ORDER_FAIL",
                                    f"{total:.4f}股失败", "立即人工")
            current = self.executor.get_current_price() or 0
            self._emergency_clear_fifo(current)
            self._running = False
            return

        fill = self.executor.wait_for_order_fill(oid, timeout_sec=30)
        if not fill:
            self.db.log_risk_event("EMERGENCY_TIMEOUT",
                                    f"{total:.4f}股超时", "立即人工")
            current = self.executor.get_current_price() or 0
            self._emergency_clear_fifo(current)
            self._running = False
            return

        fill_price = fill["fill_price"]
        filled = fill["quantity"]
        total_com = fill["commission"]

        if abs(filled - total) > 0.01:
            self.db.log_risk_event("EMERGENCY_PARTIAL",
                                    f"{filled:.4f}/{total:.4f}",
                                    "核查持仓")

        if filled > 0:
            grid_filled = min(grid_shares, filled)
            base_filled = filled - grid_filled
        else:
            grid_filled = 0
            base_filled = 0

        if grid_filled > 0.0001:
            gc = total_com * (grid_filled / filled)
            result = self.pnl.record_sell(
                quantity=grid_filled, price=fill_price,
                commission=gc, level_index=-9998
            )
            epnl = result.net_pnl if result else 0.0
            self.db.log_trade(
                "SELL", config.SYMBOL, grid_filled, fill_price,
                order_type="EMERGENCY_GRID", commission=gc, pnl=epnl,
                note=f"硬止损-网格 @${fill_price:.2f}"
            )

        if base_filled > 0.0001:
            bc = total_com * (base_filled / filled)
            self.db.log_trade(
                "SELL", config.SYMBOL, base_filled, fill_price,
                order_type="EMERGENCY_BASE", commission=bc, pnl=0.0,
                note=f"硬止损-底仓 @${fill_price:.2f}"
            )

        # 防御性二次清算
        if self.pnl.buy_queue:
            logger.warning(f"record_sell 后仍有 {len(self.pnl.buy_queue)} 笔, 强制清算")
            self.pnl.force_liquidate_queue(
                fill_price=fill_price, total_commission=0,
                reason="emergency_residual"
            )

        self.pnl.save_state()
        # 硬止损: 清空网格 + 回到 SCANNING
        self.grid = None
        self._base_position_shares = 0.0
        self._persist_all()
        self.db.log_risk_event(
            "EMERGENCY_LIQUIDATE",
            f"清{filled:.4f}@${fill_price:.2f} 佣${total_com:.2f}",
            "硬止损 队列已清"
        )
        self._log_daily_snapshot_now(reason="emergency_liquidate")
        self._running = False

    def _emergency_clear_fifo(self, fallback_price: float):
        if not self.pnl.buy_queue:
            self.pnl.save_state()
            return
        count = len(self.pnl.buy_queue)
        self.pnl.force_liquidate_queue(
            fill_price=fallback_price, total_commission=0,
            reason="emergency_fallback_no_real_fill"
        )
        self.pnl.save_state()
        self.db.log_risk_event(
            "FIFO_FORCE_CLEAR",
            f"兜底价${fallback_price:.2f}清{count}笔",
            "PnL可能不准"
        )

    # ───────────────────────────────
    #  daily_snapshots 写入
    # ───────────────────────────────

    def _log_daily_snapshot_now(self, reason: str = "") -> None:
        """覆盖当日 daily_snapshot.

        触发时机:
          - 主循环每个新交易日的第一次步进 (_maybe_log_daily_snapshot)
          - 底仓建仓完成 → ACTIVE_GRID 之后
          - 网格 BUY/SELL 成交后 (_handle_active_grid)
          - EXIT 完成 (_finalize_exit)
          - 紧急清仓 (_emergency_liquidate)
          - 周报生成时 (兼容旧路径)

        覆盖语义由 trade_logger.log_daily_snapshot 的
        INSERT OR REPLACE + UNIQUE(date) 保证, 同一天多次写入只保留最新一条.
        失败不致命, 只写一行警告日志.
        """
        try:
            pos = self.executor.get_position_details() or {}
            summary = self.executor.get_account_summary() or {}
            equity = float(summary.get("NetLiquidation", 0) or 0)
            cash = float(summary.get("TotalCashValue", 0) or 0)
            shares = float(pos.get("shares", 0) or 0)
            position_value = float(pos.get("market_value", 0) or 0)
            unrealized = float(pos.get("unrealized_pnl", 0) or 0)
            try:
                today_pnl = float(self.pnl.get_today_pnl() or 0)
            except Exception:
                today_pnl = 0.0
            grid_center = float(self.grid.center_price) if self.grid else 0.0
            state_value = self.state_machine.state.value

            self.db.log_daily_snapshot(
                state=state_value,
                total_equity=equity,
                position_shares=shares,
                position_value=position_value,
                cash=cash,
                unrealized_pnl=unrealized,
                realized_pnl_today=today_pnl,
                grid_center=grid_center,
                note=reason,
            )
            self._last_snapshot_date = self.clock.now().date()
            logger.info(
                f"📸 daily snapshot updated | reason={reason or 'periodic'} "
                f"| state={state_value} | shares={shares:.2f} "
                f"| equity=${equity:,.2f} | cash=${cash:,.2f} "
                f"| grid_center=${grid_center:.2f}"
            )
        except Exception as e:
            logger.warning(f"daily snapshot 写入失败 (非致命): {e}")

    def _maybe_log_daily_snapshot(self) -> None:
        """主循环节拍调用: 每个新交易日的第一次步进写一次 snapshot.

        关键事件 (entry/fill/exit/紧急清仓) 直接调 _log_daily_snapshot_now,
        不依赖这里. 这里只负责 "无事件的 idle 日子也要留下当天权益记录".
        """
        today = self.clock.now().date()
        if self._last_snapshot_date == today:
            return
        self._log_daily_snapshot_now(reason="daily_periodic")

    # ───────────────────────────────
    #  周报 (每周一 16:30 ET)
    # ───────────────────────────────

    def _maybe_generate_weekly_report(self):
        """改为每周生成一次 (原来每日)"""
        # B3: naive 时间的来源要区分:
        #   LiveClock  (实盘): datetime.now() = 系统本地 TZ, 未必是 ET,
        #       直接贴 tzinfo=ET 标签会在非 ET 服务器上错开几小时.
        #       对策: 重新用 tz=ET 取一次.
        #   HistoricalClock (回测): df.index 已 tz_convert 到 NY 后 strip tz,
        #       naive 时间就是 ET wall-clock, 直接使用.
        from interfaces import HistoricalClock
        raw_now = self.clock.now()
        try:
            if raw_now.tzinfo:
                now = raw_now.astimezone(ET)
            elif isinstance(self.clock, HistoricalClock):
                now = raw_now
            else:
                now = datetime.now(ET).replace(tzinfo=None)
        except Exception:
            now = raw_now

        iso_year, iso_week, weekday = now.isocalendar()

        # 只在周一 (weekday=1) 之后, 且本周未生成过
        if weekday != 1:
            return
        if self._last_weekly_report_week == (iso_year, iso_week):
            return

        target_min = config.WEEKLY_REPORT_HOUR * 60 + config.WEEKLY_REPORT_MINUTE
        cur_min = now.hour * 60 + now.minute
        if cur_min < target_min:
            return

        self._generate_weekly_report()
        self._last_weekly_report_week = (iso_year, iso_week)

    def _generate_weekly_report(self):
        """生成周报快照"""
        try:
            # 当日 snapshot 走统一入口, 避免与主循环/事件触发的写入逻辑漂移
            self._log_daily_snapshot_now(reason="weekly_report")

            pos = self.executor.get_position_details()
            summary = self.executor.get_account_summary()
            cash = summary.get("TotalCashValue", 0)
            equity = summary.get("NetLiquidation", 0)

            self.pnl.save_state()

            # 真实 realized PnL (账户级, 权威)
            account_realized = self.executor.get_realized_pnl()
            win_stats = self.pnl.get_win_rate()

            logger.info(
                f"📊 周报 | 权益${equity:.2f} | "
                f"账户实现${account_realized:+.2f}" if account_realized is not None
                else f"📊 周报 | 权益${equity:.2f}"
            )
            logger.info(
                f"   胜率{win_stats['win_rate']:.1f}% | "
                f"{win_stats['wins']}胜/{win_stats['losses']}负 | "
                f"本地PnL${win_stats['total_pnl']:+.2f}"
            )

            # HTML 报告 — 用当前 bot 的 db (回测时是 tempdir, 实盘是 config.DB_FILE)
            try:
                from report_generator import ReportGenerator
                gen = ReportGenerator(db_path=self.db.db_path)
                account_data = {
                    "state": self.state_machine.state.value,
                    "equity": equity,
                    "shares": pos["shares"],
                    "cash": cash,
                    "unrealized_pnl": pos.get("unrealized_pnl", 0),
                    "realized_pnl": account_realized,
                    "win_rate": win_stats,
                }
                grid_data = None
                if self.grid:
                    grid_data = {
                        "center": self.grid.center_price,
                        "spacing_pct": self.grid.spacing_pct,
                        "atr": self.grid.atr_at_init,
                        "filled_buys": self.grid.total_filled_buys,
                        "filled_sells": self.grid.total_filled_sells,
                    }
                path = gen.generate_weekly_report(account_data, grid_data)
                logger.info(f"   报告: {path}")
            except Exception as e:
                logger.error(f"周报HTML生成失败: {e}")

            # 重置日内风控
            self.risk.reset_daily_flags()
            p = self.executor.get_current_price()
            if p:
                self.risk.set_prev_close(p)

        except Exception as e:
            logger.error(f"周报生成失败: {e}", exc_info=True)
