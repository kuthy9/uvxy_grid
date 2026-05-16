"""
account_risk.py — 账户级风控 (多标的共享)

单标的时不需要这个模块: 原 RiskManager 已经覆盖全部场景, account_risk=None
即退回到旧行为.

多标的场景下:
  - 同一账户的 N 个 GridBot 共用一个 AccountRiskManager 实例
  - NetLiquidation 缓存读取 (TTL), 避免 N 个 bot 每 step 重复打 IBKR API
  - 硬止损 (账户级权益跌幅) / 日亏损 (跨 symbol PnL 聚合) 在这里统一判定
  - 共享 sqlite 文件 (账户级 db_path), 与 per-bot 的 trades DB 分开
  - per-bot RiskManager 仍然保留: flash crash / earnings / position limit 是 per-symbol

数据库 schema (启动幂等建表):
  - account_risk_state: 单行 id=1, 持当前 _hard_stop_triggered / _daily_loss_triggered /
    last_reset_date 等触发后会卡住状态的 flag.
  - account_trade_pnl: 多 bot 上报的逐笔成交 PnL, 用于日亏损跨 symbol 聚合查询.

幂等性: __init__ 重复调用 / load_state 重复调用都安全.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger("GridTrader.AccountRisk")
ET = ZoneInfo("America/New_York")


class RiskCheckResult:
    """与 RiskManager.RiskCheckResult 同结构, 避免循环 import 用副本."""
    def __init__(self, allowed: bool, reason: str = ""):
        self.allowed = allowed
        self.reason = reason

    def __bool__(self):
        return self.allowed


class AccountRiskManager:
    """账户级共享风控. 一进程一实例, 由 orchestrator/bot_factory 创建后注入各 RiskManager.

    Args:
        db_path: 共享账户级 DB 路径 (与 per-symbol trades DB 不同, 避免命名空间混淆).
        clock:   注入的 Clock (LiveClock / HistoricalClock).
        total_capital: 账户总资金参考值. None → 用 config.TOTAL_CAPITAL.
        equity_cache_ttl_sec: NetLiquidation 缓存 TTL. 多 bot 同 tick 调 get_account_equity
                              共用一份缓存, 避免重复读取.
    """

    def __init__(self, db_path: str, clock,
                 total_capital: Optional[float] = None,
                 equity_cache_ttl_sec: float = 10.0):
        self.db_path = db_path
        self.clock = clock
        self._total_capital_override = total_capital
        self._equity_cache_ttl = float(equity_cache_ttl_sec)
        self._equity_cache_value: Optional[float] = None
        self._equity_cache_at: Optional[datetime] = None

        # 触发后会持续生效的 flag — 多 bot 共享 (一个 bot 触发后所有 bot 都被冻结)
        self._hard_stop_triggered = False
        self._daily_loss_triggered = False
        self._last_reset_date: Optional[str] = None

        self._init_db()
        self._load_state()

    @property
    def total_capital(self) -> float:
        if self._total_capital_override is not None:
            return float(self._total_capital_override)
        # 退回 config.TOTAL_CAPITAL — 单标的旧行为入口.
        # 配置未注入时返回 0 (而不是抛异常), 让运维 CLI / 状态查询仍可工作;
        # 真正需要分母的 check_hard_stop / check_daily_loss 在 0 时已自带 early return.
        try:
            return float(config.require_total_capital())
        except Exception:
            return 0.0

    # ──────────────────────────
    #  DB schema
    # ──────────────────────────

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS account_risk_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    hard_stop_triggered INTEGER DEFAULT 0,
                    daily_loss_triggered INTEGER DEFAULT 0,
                    last_reset_date TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS account_trade_pnl (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    net_pnl REAL NOT NULL,
                    note TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_account_trade_pnl_date
                    ON account_trade_pnl(date);
                CREATE INDEX IF NOT EXISTS idx_account_trade_pnl_symbol
                    ON account_trade_pnl(symbol);

                CREATE TABLE IF NOT EXISTS account_risk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    action_taken TEXT
                );
            """)

    def _save_state(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO account_risk_state
                       (id, hard_stop_triggered, daily_loss_triggered,
                        last_reset_date, updated_at)
                       VALUES (1, ?, ?, ?, ?)""",
                    (1 if self._hard_stop_triggered else 0,
                     1 if self._daily_loss_triggered else 0,
                     self._last_reset_date,
                     self.clock.now().isoformat())
                )
        except Exception as e:
            logger.warning(f"_save_state 失败 (非致命): {e}")

    def _load_state(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    """SELECT hard_stop_triggered, daily_loss_triggered, last_reset_date
                       FROM account_risk_state WHERE id = 1"""
                ).fetchone()
            if row:
                self._hard_stop_triggered = bool(row[0])
                self._daily_loss_triggered = bool(row[1])
                self._last_reset_date = row[2]
                if self._hard_stop_triggered or self._daily_loss_triggered:
                    logger.warning(
                        f"📂 AccountRisk 恢复: hard_stop={self._hard_stop_triggered} "
                        f"daily_loss={self._daily_loss_triggered} reset_date={self._last_reset_date}"
                    )
        except Exception as e:
            logger.warning(f"_load_state 失败 (非致命): {e}")

    def _log_event(self, event_type: str, details: str = "", action: str = ""):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO account_risk_events
                       (timestamp, event_type, details, action_taken)
                       VALUES (?, ?, ?, ?)""",
                    (self.clock.now().isoformat(), event_type, details, action)
                )
        except Exception as e:
            logger.warning(f"_log_event 失败 (非致命): {e}")

    # ──────────────────────────
    #  NetLiquidation 缓存
    # ──────────────────────────

    def get_account_equity(self, executor) -> float:
        """缓存的 NetLiquidation 读取. 多 bot 同 tick 共用同一份, 避免 IBKR 重复调用.

        executor 必须有 get_account_summary() 方法 (Executor 协议).
        """
        now = self.clock.now()
        if (self._equity_cache_at is None
                or (now - self._equity_cache_at).total_seconds() >= self._equity_cache_ttl):
            try:
                summary = executor.get_account_summary() or {}
                value = float(summary.get("NetLiquidation", 0) or 0)
                if value > 0:
                    self._equity_cache_value = value
                    self._equity_cache_at = now
                elif self._equity_cache_value is None:
                    # 首次读取失败 → 退回 total_capital, 但不缓存避免污染下次
                    return self.total_capital
            except Exception as e:
                logger.warning(f"get_account_equity 读取失败 (非致命): {e}")
                if self._equity_cache_value is None:
                    return self.total_capital
        return float(self._equity_cache_value or self.total_capital)

    def invalidate_equity_cache(self):
        """成交 / 强制刷新场景调用. 下次 get_account_equity 一定会重读."""
        self._equity_cache_at = None

    # ──────────────────────────
    #  风控判定 (账户级, 与单标的 RiskManager 同语义但跨 symbol 共享)
    # ──────────────────────────

    def check_hard_stop(self, account_equity: Optional[float] = None,
                        executor=None) -> RiskCheckResult:
        """硬止损 (账户级).
        优先使用传入 account_equity; 否则从 executor 读 (缓存); 都没有则退到 total_capital."""
        if self._hard_stop_triggered:
            return RiskCheckResult(False, "硬止损已触发 (账户级)")

        if account_equity is None and executor is not None:
            account_equity = self.get_account_equity(executor)
        if account_equity is None or account_equity <= 0:
            # 取不到 equity → 不阻塞, 走默认放行 (避免空数据误杀)
            return RiskCheckResult(True)

        total = self.total_capital
        if total <= 0:
            return RiskCheckResult(True)
        loss_pct = (total - account_equity) / total
        if loss_pct >= config.HARD_STOP_LOSS_PCT:
            self._hard_stop_triggered = True
            self._save_state()
            self._log_event(
                "HARD_STOP",
                f"亏损 {loss_pct*100:.1f}% (account_equity=${account_equity:.2f} "
                f"total_capital=${total:.2f})",
                "全账户冻结所有 bot, 各自走 emergency_liquidate"
            )
            logger.critical(
                f"🚨 账户级硬止损 触发 — 亏损 {loss_pct*100:.1f}%, 所有 bot 冻结"
            )
            return RiskCheckResult(False, f"账户级硬止损 -{loss_pct*100:.1f}%")
        return RiskCheckResult(True)

    def check_daily_loss(self) -> RiskCheckResult:
        """跨 symbol 聚合的当日已实现 PnL 上限."""
        if self._daily_loss_triggered:
            return RiskCheckResult(False, "日亏损上限 (账户级)")
        today_pnl = self.get_today_account_realized_pnl()
        limit = self.total_capital * config.MAX_DAILY_LOSS_PCT
        if today_pnl < -limit:
            self._daily_loss_triggered = True
            self._save_state()
            self._log_event(
                "DAILY_LIMIT",
                f"账户日亏损 ${today_pnl:.2f} > 上限 ${limit:.2f} "
                f"({config.MAX_DAILY_LOSS_PCT*100:.1f}%)",
                "暂停所有 bot 当日交易"
            )
            return RiskCheckResult(False, f"账户日亏损 ${today_pnl:.2f}")
        return RiskCheckResult(True)

    def check_trading_hours(self) -> RiskCheckResult:
        """交易时段判定 — 与 RiskManager.check_trading_hours 同语义."""
        from interfaces import HistoricalClock
        now_raw = self.clock.now()
        if now_raw.tzinfo is not None:
            now = now_raw.astimezone(ET).replace(tzinfo=None)
        elif isinstance(self.clock, HistoricalClock):
            now = now_raw
        else:
            now = datetime.now(ET).replace(tzinfo=None)
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

    def is_hard_stopped(self) -> bool:
        return self._hard_stop_triggered

    def is_daily_loss_stopped(self) -> bool:
        return self._daily_loss_triggered

    # ──────────────────────────
    #  跨 symbol PnL 聚合
    # ──────────────────────────

    def contribute_trade_pnl(self, symbol: str, net_pnl: float,
                              note: str = "") -> None:
        """per-bot 在每次 SELL 平仓 (含 EXIT/紧急清算) 后调用, 把该笔 net_pnl 写入共享表.

        timestamp 用 self.clock.now() 保证回测时间一致; date 字段 (YYYY-MM-DD) 用于
        check_daily_loss 的 WHERE 索引.
        """
        if not symbol:
            return
        try:
            now = self.clock.now()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO account_trade_pnl
                       (timestamp, date, symbol, net_pnl, note)
                       VALUES (?, ?, ?, ?, ?)""",
                    (now.isoformat(), now.strftime("%Y-%m-%d"),
                     symbol, float(net_pnl), note)
                )
        except Exception as e:
            logger.warning(f"contribute_trade_pnl 失败 (非致命): {e}")

    def get_today_account_realized_pnl(self) -> float:
        """跨 symbol 聚合 today 已实现 PnL. 用 self.clock 的 today, 而不是 wall-clock."""
        today = self.clock.now().strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(net_pnl), 0) FROM account_trade_pnl WHERE date = ?",
                    (today,)
                ).fetchone()
            return float(row[0]) if row else 0.0
        except Exception as e:
            logger.warning(f"get_today_account_realized_pnl 失败 (非致命): {e}")
            return 0.0

    # ──────────────────────────
    #  日重置 / 人工恢复
    # ──────────────────────────

    def reset_daily_flags(self):
        """周报 / 日切时调. 与 RiskManager.reset_daily_flags 同语义."""
        self._daily_loss_triggered = False
        self._last_reset_date = self.clock.now().strftime("%Y-%m-%d")
        self.invalidate_equity_cache()
        self._save_state()

    def manual_resume(self):
        """人工确认后清除全部账户级 flag (硬止损 + 日亏损).
        仅紧急运维使用, 通常不应被代码自动调用. 等价于 manual_resume_hard_stop()
        + manual_resume_daily_loss() 顺序调用."""
        self.manual_resume_hard_stop()
        self.manual_resume_daily_loss()

    def manual_resume_hard_stop(self):
        """单独清除硬止损 flag. 用于人工判断 "可以重启交易, 但日亏损上限保持" 的场景."""
        if not self._hard_stop_triggered:
            logger.info("manual_resume_hard_stop: hard_stop 未触发, no-op")
            return
        self._hard_stop_triggered = False
        self._save_state()
        self.invalidate_equity_cache()
        self._log_event(
            "MANUAL_RESUME_HARD_STOP",
            "硬止损 flag 已被人工清除",
            "允许后续 can_trade 通过 hard_stop 检查"
        )
        logger.warning("⚠️ manual_resume_hard_stop: hard_stop flag 已清除")

    def manual_resume_daily_loss(self):
        """单独清除日亏损 flag. 例如人工判断昨日大亏后, 想限制今天再亏多少 — 清掉后重新计数."""
        if not self._daily_loss_triggered:
            logger.info("manual_resume_daily_loss: daily_loss 未触发, no-op")
            return
        self._daily_loss_triggered = False
        self._save_state()
        self._log_event(
            "MANUAL_RESUME_DAILY_LOSS",
            "日亏损 flag 已被人工清除",
            "允许后续 can_trade 通过 daily_loss 检查"
        )
        logger.warning("⚠️ manual_resume_daily_loss: daily_loss flag 已清除")

    def get_status(self) -> dict:
        """运维查询用. 返回 dict 含触发态 + 当日聚合 PnL + reset 日期."""
        return {
            "db_path": self.db_path,
            "hard_stop_triggered": self._hard_stop_triggered,
            "daily_loss_triggered": self._daily_loss_triggered,
            "last_reset_date": self._last_reset_date,
            "today_realized_pnl": self.get_today_account_realized_pnl(),
            "total_capital": self.total_capital,
            "equity_cache_value": self._equity_cache_value,
            "equity_cache_at": (
                self._equity_cache_at.isoformat() if self._equity_cache_at else None
            ),
        }
