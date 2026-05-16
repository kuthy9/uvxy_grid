"""
risk_manager.py — 风控 (v2.3)

v2.3 (2026-04-23):
  - 注入 clock (LiveClock / HistoricalClock) 消除 wall-clock 污染
  - 所有时间戳都走 self.clock.now(), backtest 时间基于历史 bar, 不再漏洞
  - 删除 HistoricalRiskManager 子类 (职责已内化)

v2.2:
  - 闪崩保护在盘中重启后失效的问题 (Bug 3)
  - 新增 initialize_from_db() 启动时从数据库恢复 _prev_close
  - 新增 intraday 滚动参考价, 避免盘中暴跌到-7.9%但单日累计-4%时保护失效
"""

import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger("GridTrader.Risk")
ET = ZoneInfo("America/New_York")


class RiskCheckResult:
    def __init__(self, allowed: bool, reason: str = ""):
        self.allowed = allowed
        self.reason = reason

    def __bool__(self):
        return self.allowed


class RiskManager:
    def __init__(self, db, clock=None,
                 allocated_capital: Optional[float] = None,
                 account_risk=None,
                 capital_provider=None,
                 symbol: Optional[str] = None):
        """
        db               : TradeDatabase (或 TradeEventCollector, 兼容)
        clock            : Clock 协议实例. None = LiveClock (即系统时间).
        allocated_capital: 多标的支持 — 本 bot 的资金分配额 (USD).
                           None → fallback config.require_total_capital() (单标的旧行为).
        account_risk     : 共享账户级 AccountRiskManager. 多标的时传同一个实例;
                           None → 单标的旧行为 (per-bot 独立风控).
        capital_provider : 保留接口 (Phase 4.B 动态资金). 当前版本吸收, 不启用.
        symbol           : 标的代码 (用于日志 / on_trade_closed). 可选.
        """
        if clock is None:
            from interfaces import LiveClock
            clock = LiveClock()
        self.db = db
        self.clock = clock
        self._allocated_capital = allocated_capital
        self.account_risk = account_risk   # 可由外部读取 (测试 / orchestrator)
        self._capital_provider = capital_provider  # Phase 4.B 预留, 当前不使用
        self._symbol = symbol
        self._daily_loss_triggered = False
        self._hard_stop_triggered = False
        self._flash_crash_triggered = False
        self._prev_close: Optional[float] = None
        # 盘中滚动参考价
        self._intraday_snapshots: list[tuple[datetime, float]] = []
        # Bug I: 最新 ATR% (用于动态闪崩阈值)
        self._current_atr_pct: Optional[float] = None

    def _capital_reference(self) -> float:
        """返回风控 baseline 资金值.

        2026-05-15 Phase 4.A: 多标的传 allocated_capital, 单标的 fallback
        config.require_total_capital(). 修复 sub-bot 启动时虚假 hard_stop bug.
        """
        if self._allocated_capital is not None:
            return float(self._allocated_capital)
        return float(config.require_total_capital())

    def on_trade_closed(self, symbol: str, net_pnl: float,
                        note: str = "") -> None:
        """平仓事件通知.

        单标的: no-op (不报错).
        多标的 (account_risk 存在): 写入共享 account_trade_pnl + 失效 equity 缓存.
        """
        if self.account_risk is None:
            return
        try:
            self.account_risk.contribute_trade_pnl(symbol, net_pnl, note=note)
            self.account_risk.invalidate_equity_cache()
        except Exception as e:
            logger.warning(f"on_trade_closed 写入 account_risk 失败 (非致命): {e}")

    def _now(self) -> datetime:
        """当前时间 — 统一通过 clock, backtest/live 都安全"""
        return self.clock.now()

    def _now_et(self) -> datetime:
        """返回 ET 自然时间 (naive), 用于交易时段判定.
        HistoricalClock: 其 naive 时间已是 ET wall-clock (由 backtest driver 保证).
        LiveClock: 其 naive 时间是系统时区, 需转 ET.
        """
        from interfaces import HistoricalClock
        now = self._now()
        if now.tzinfo is not None:
            return now.astimezone(ET).replace(tzinfo=None)
        if isinstance(self.clock, HistoricalClock):
            return now  # 回测: naive = ET 自然时间
        # LiveClock: 系统本地 naive → 重新用 ET 取一次
        return datetime.now(ET).replace(tzinfo=None)

    def initialize_from_db(self, current_price: float = None,
                           broker_prev_close: float = None):
        """
        启动时调用: 恢复 _prev_close, 避免盘中重启后闪崩保护失效

        优先级 (v2.4, 冷启动友好):
          1. risk_state 快照 (<24h)             — 上次进程末状态, 语义最贴
          2. broker_prev_close (IBKR 历史日线)   — 冷启动权威来源, 真正的上一日 close
          3. daily_snapshots.grid_center         — 粗略兜底 (center ≠ close)
          4. current_price                        — 最后兜底 (当前价, 语义已偏, 会 WARN)
          5. None → _prev_close 无法初始化, 闪崩保护依赖层2 (窗口内 ATR 阈值) 运行
        """
        self._ensure_state_table()

        restored = None
        restored_source = None
        with sqlite3.connect(self.db.db_path) as conn:
            # 策略1: 从 risk_state 读最新快照
            row = conn.execute(
                """SELECT timestamp, prev_close FROM risk_state
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            if row:
                ts_str, saved_price = row
                ts = datetime.fromisoformat(ts_str)
                age_hours = (self._now() - ts).total_seconds() / 3600
                if age_hours < 24 and saved_price:
                    restored = float(saved_price)
                    restored_source = "risk_state"
                    logger.info(f"✓ 从数据库恢复 _prev_close=${restored:.2f} "
                                f"(快照 {age_hours:.1f}h 前)")

            # 策略2: broker 历史日线 (冷启动权威)
            if restored is None and broker_prev_close and broker_prev_close > 0:
                restored = float(broker_prev_close)
                restored_source = "broker_hist"
                logger.info(f"✓ 从 broker 历史日线恢复 _prev_close=${restored:.2f}")

            # 策略3: 数据库最近的 daily_snapshot (表可能还不存在)
            if restored is None:
                try:
                    row = conn.execute(
                        """SELECT date, grid_center FROM daily_snapshots
                           WHERE grid_center > 0 ORDER BY date DESC LIMIT 1"""
                    ).fetchone()
                    if row and row[1]:
                        restored = float(row[1])
                        restored_source = "daily_snapshot"
                        logger.warning(f"⚠️ 用最近daily_snapshot的grid_center=${restored:.2f} 兜底, 精度有限")
                except sqlite3.OperationalError:
                    pass  # 表不存在 (首次启动正常), 跳到策略4

        # 策略4: 当前市价兜底 (语义已偏, 仅避免闪崩保护完全瘫痪)
        if restored is None and current_price:
            restored = current_price
            restored_source = "current_price"
            logger.warning(f"⚠️ 无历史数据, 用当前市价${restored:.2f}作为闪崩基准 "
                           f"(首次启动正常, 如果是重启则丢失了启动前的跌幅信息)")

        self._prev_close = restored

        if restored is None:
            logger.error("🚨 _prev_close 无法初始化, 闪崩保护 (相对前收) 失效直到下次日报; "
                         "盘中窗口 ATR 阈值仍生效")
        else:
            logger.debug(f"_prev_close 恢复来源: {restored_source}")

        # 同时把当前价加入盘中滚动快照
        if current_price:
            self._intraday_snapshots.append((self._now(), current_price))

    def _ensure_state_table(self):
        """确保 risk_state 表存在"""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    prev_close REAL,
                    note TEXT
                )
            """)

    def _persist_prev_close(self, price: float, note: str = ""):
        """把 _prev_close 写入数据库以便重启恢复"""
        self._ensure_state_table()
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute(
                "INSERT INTO risk_state (timestamp, prev_close, note) VALUES (?, ?, ?)",
                (self._now().isoformat(), price, note)
            )

    def record_intraday_price(self, price: float):
        """
        盘中滚动价格 (主循环每次取到价格时调用).
        v2.3: 基于 self.clock.now(), backtest/live 统一.

        Bug I 修复: 窗口长度跟随策略周期
          - 4h 策略: 8h 窗口 (2个bar)
          - 日/1h 策略: 2h 窗口
        """
        now = self._now()
        self._intraday_snapshots.append((now, price))
        try:
            strategy_hours = config.strategy_interval_hours()
            window_hours = max(2, strategy_hours * 2)
        except Exception:
            window_hours = 2
        cutoff = now - timedelta(hours=window_hours)
        self._intraday_snapshots = [
            (t, p) for (t, p) in self._intraday_snapshots if t >= cutoff
        ]

    def can_trade(self, current_price: float, account_equity: float,
                  position_value: float) -> RiskCheckResult:
        for check in [
            self.check_hard_stop(account_equity),
            self.check_daily_loss(),
            self.check_trading_hours(),
            self.check_earnings_freeze(),
            self.check_position_limit(position_value),
            self.check_flash_crash(current_price),
        ]:
            if not check:
                return check
        return RiskCheckResult(True)

    def check_trading_hours(self) -> RiskCheckResult:
        now = self._now_et()
        weekday = now.weekday()
        if weekday >= 5:
            return RiskCheckResult(False, "周末")
        cm = now.hour * 60 + now.minute
        sm = config.TRADING_START_HOUR * 60 + config.TRADING_START_MINUTE
        em = config.TRADING_END_HOUR * 60 + config.TRADING_END_MINUTE
        if weekday == 4:
            em = config.FRIDAY_CUTOFF_HOUR * 60 + config.FRIDAY_CUTOFF_MINUTE
        if cm < sm:
            return RiskCheckResult(False, "未到交易时间")
        if cm > em:
            return RiskCheckResult(False, "已过截止时间")
        return RiskCheckResult(True)

    def check_hard_stop(self, equity: float) -> RiskCheckResult:
        # 多标的: 账户级硬止损委托给 account_risk (已聚合所有 bot)
        if self.account_risk is not None:
            return self.account_risk.check_hard_stop(account_equity=equity)
        # 单标的: per-bot 旧逻辑, 基准资金改用 _capital_reference()
        if self._hard_stop_triggered:
            return RiskCheckResult(False, "硬止损已触发")
        baseline = self._capital_reference()
        loss_pct = (baseline - equity) / baseline
        if loss_pct >= config.HARD_STOP_LOSS_PCT:
            self._hard_stop_triggered = True
            self.db.log_risk_event("HARD_STOP",
                                    f"亏损 {loss_pct*100:.1f}%", "全部清仓")
            logger.critical(f"🚨 硬止损 亏损{loss_pct*100:.1f}%")
            return RiskCheckResult(False, f"硬止损 -{loss_pct*100:.1f}%")
        return RiskCheckResult(True)

    def check_daily_loss(self) -> RiskCheckResult:
        # 多标的: 账户级日亏损委托给 account_risk (已聚合所有 bot)
        if self.account_risk is not None:
            return self.account_risk.check_daily_loss()
        # 单标的: per-bot 旧逻辑
        if self._daily_loss_triggered:
            return RiskCheckResult(False, "单日亏损上限")
        today_pnl = self.db.get_today_realized_pnl()
        limit = self._capital_reference() * config.MAX_DAILY_LOSS_PCT
        if today_pnl < -limit:
            self._daily_loss_triggered = True
            self.db.log_risk_event(
                "DAILY_LIMIT",
                f"日亏损 ${today_pnl:.2f} > 上限 ${limit:.2f} "
                f"({config.MAX_DAILY_LOSS_PCT*100:.1f}%)",
                "暂停交易"
            )
            return RiskCheckResult(False, f"日亏损 ${today_pnl:.2f}")
        return RiskCheckResult(True)

    def check_earnings_freeze(self) -> RiskCheckResult:
        today = self._now().date()
        for s in config.EARNINGS_DATES:
            try:
                ed = date.fromisoformat(s)
            except ValueError:
                continue
            start = ed - timedelta(days=config.EARNINGS_FREEZE_DAYS_BEFORE)
            end = ed + timedelta(days=config.EARNINGS_FREEZE_DAYS_AFTER)
            if start <= today <= end:
                return RiskCheckResult(False, f"财报冻结 ({start}~{end})")
        return RiskCheckResult(True)

    def check_position_limit(self, position_value: float) -> RiskCheckResult:
        max_v = self._capital_reference() * config.MAX_POSITION_VALUE_PCT
        if position_value >= max_v:
            return RiskCheckResult(False, f"持仓${position_value:.0f}>上限${max_v:.0f}")
        return RiskCheckResult(True)

    def check_flash_crash(self, price: float) -> RiskCheckResult:
        """
        闪崩保护: 两层检测
          1. 相对前收盘价 > -8% (大幅累计下跌)
          2. 相对过去窗口内最高价的动态阈值 (max(5%, 3×ATR%))
        """
        if self._flash_crash_triggered:
            return RiskCheckResult(False, "闪崩保护")

        # 层1: 相对前收盘
        if self._prev_close:
            chg = (price - self._prev_close) / self._prev_close
            if chg <= -0.08:
                self._flash_crash_triggered = True
                self.db.log_risk_event(
                    "FLASH_CRASH",
                    f"相对前收${self._prev_close:.2f}跌{chg*100:.1f}%", "暂停"
                )
                return RiskCheckResult(False, f"闪崩 {chg*100:.1f}%")

        # 层2: 相对窗口内最高价 (动态阈值, 适配策略周期)
        if self._intraday_snapshots:
            recent_high = max(p for (_, p) in self._intraday_snapshots)
            if recent_high > 0:
                intraday_chg = (price - recent_high) / recent_high
                threshold = -0.05
                if self._current_atr_pct and self._current_atr_pct > 0:
                    threshold = min(-0.05, -3 * self._current_atr_pct)
                if intraday_chg <= threshold:
                    self._flash_crash_triggered = True
                    self.db.log_risk_event(
                        "FLASH_CRASH_INTRADAY",
                        f"窗口内从${recent_high:.2f}跌{intraday_chg*100:.1f}% "
                        f"(阈值{threshold*100:.1f}%)", "暂停"
                    )
                    return RiskCheckResult(False,
                                           f"盘中闪崩 {intraday_chg*100:.1f}%")

        return RiskCheckResult(True)

    def update_atr_context(self, atr_pct: float):
        """让主循环把最新 ATR% 传入, 供 check_flash_crash 动态计算阈值"""
        self._current_atr_pct = atr_pct

    def set_prev_close(self, price: float):
        """设置前收盘价并持久化"""
        self._prev_close = price
        self._persist_prev_close(price, "daily_update")

    def reset_daily_flags(self):
        self._daily_loss_triggered = False
        self._flash_crash_triggered = False
        # 每日重置滚动快照
        self._intraday_snapshots.clear()

    def is_hard_stopped(self) -> bool:
        # 多标的: 委托给共享 account_risk (账户级状态, 跨 bot 可见)
        if self.account_risk is not None:
            return self.account_risk.is_hard_stopped()
        return self._hard_stop_triggered

    def manual_resume(self):
        self._flash_crash_triggered = False
