"""
session_manager.py — Aggressive Tactical Session Grid 的会话生命周期

一个 Session = 一轮 "从开仓到清仓" 的网格战役.
SessionManager 负责:
  - 在 grid_bot 入场成功后 start_session(...)
  - 主循环每个 step 调 update_session(...) 把瞬时 equity/价格喂进来
  - 调 evaluate_session(...) 综合 tactical_rules 给出 (action, reason)
  - 自身只管自己的状态 + 把变更落到 grid_sessions / grid_session_events 表

它**不**碰真实下单 / 不碰 IBKR / 不碰 StateMachine 主状态.
具体动作 (取消挂单 / 切到 DEFENSIVE / EXIT_PENDING) 由 grid_bot 看 evaluate 的输出后执行.

模式语义 (与 tactical_config 一致):
  - offensive  : 正常网格
  - defensive  : 取消 BUY, 只允许 SELL
  - exiting    : 进入 EXIT_PENDING 阶段 (本 session 等待清仓)
  - cooldown   : 上一轮退出后短暂冷却, 不开新 session

设计取向:
  - 与 StateMachine 解耦: SessionManager 知道自己处于哪个 mode, StateMachine 知道整体 SystemState.
    映射关系: OFFENSIVE_GRID ↔ session.mode==offensive,
              DEFENSIVE_GRID ↔ session.mode==defensive,
              EXIT_PENDING  ↔ session.mode==exiting,
              COOLDOWN       ↔ session.mode==cooldown.
    GridBot 在状态切换时同时通知双方.

落盘策略:
  - 每次 mode 变更 / 关键事件 → grid_session_events
  - SessionContext 整体快照 → grid_sessions (INSERT OR REPLACE on session_id)
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import config
import tactical_config as tcfg
import tactical_rules as rules
from tactical_rules import MarketContext, SessionStateView

logger = logging.getLogger("GridTrader.Session")


@dataclass
class SessionContext:
    """单 session 全生命周期状态. 由 SessionManager 持有, 序列化进 grid_sessions 表."""
    session_id: str = ""
    symbol: str = ""
    started_at: str = ""
    ended_at: Optional[str] = None

    # 状态: open / closed
    status: str = "open"
    # 模式: offensive / defensive / exiting / cooldown
    mode: str = tcfg.SESSION_MODE_OFFENSIVE

    # 入场快照
    start_equity: float = 0.0
    start_cash: float = 0.0
    start_position: float = 0.0
    start_price: float = 0.0

    # 实时值 — 由 update_session 写入
    current_equity: float = 0.0
    current_cash: float = 0.0
    current_position: float = 0.0
    current_price: float = 0.0

    realized_pnl: float = 0.0     # 本 session 内 sell-side 累计 net pnl
    unrealized_pnl: float = 0.0   # 持仓浮盈 (market_value - cost_basis)
    total_pnl: float = 0.0        # realized + unrealized
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_position_value: float = 0.0
    end_equity: Optional[float] = None
    end_cash: Optional[float] = None
    end_position: Optional[float] = None
    end_price: Optional[float] = None

    age_bars: float = 0.0
    confidence: str = tcfg.CONFIDENCE_NORMAL

    exit_reason: str = ""
    defensive_reason: str = ""

    # 已成交的下方档 (-1, -2, ...). 决定 max_buy_levels 的 effective_level 计算
    filled_buy_levels: list[int] = field(default_factory=list)

    # S1: session 内已用过的 recenter 次数 (rescue + 正常 recenter 都计数).
    # tactical_rules.should_disable_recenter 用它判断 "首次零成交追价" 是否还能用.
    recenter_used_count: int = 0

    created_at: str = ""
    updated_at: str = ""

    def as_view(self) -> SessionStateView:
        """喂给 tactical_rules 的快照视图."""
        return SessionStateView(
            session_id=self.session_id,
            started_at=self.started_at,
            age_bars=self.age_bars,
            start_equity=self.start_equity,
            start_price=self.start_price,
            current_equity=self.current_equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            total_pnl=self.total_pnl,
            peak_pnl=self.peak_pnl,
            max_drawdown=self.max_drawdown,
            max_position_value=self.max_position_value,
            position_value=self.current_position * max(self.current_price, 0.0),
            filled_buy_levels=list(self.filled_buy_levels),
            mode=self.mode,
            recenter_used_count=int(self.recenter_used_count),
        )


# evaluate_session 输出的统一动作枚举
ACTION_NONE = "none"
ACTION_ENTER_DEFENSIVE = "enter_defensive"
ACTION_FORCE_EXIT = "force_exit"
ACTION_PROFIT_PROTECT_EXIT = "profit_protect_exit"
ACTION_PARTIAL_PROFIT_EXIT = "partial_profit_exit"


@dataclass
class SessionEvaluation:
    """evaluate_session 的统一输出."""
    action: str = ACTION_NONE
    reason: str = ""
    details: dict = field(default_factory=dict)
    trend_risk_score: float = 0.0
    confidence: str = tcfg.CONFIDENCE_NORMAL


class SessionManager:
    """战术 session 管理器. 不持有锁, grid_bot 串行调用即可."""

    def __init__(self, db, clock):
        """
        Args:
            db: TradeDatabase / TradeEventCollector (要求有 db_path 与 log_session_event)
            clock: Clock 协议实例
        """
        self.db = db
        self.clock = clock
        self.session: Optional[SessionContext] = None
        # cooldown 状态: 退出后由 grid_bot 标记, end_at_bar=多少 bar 后解除
        self._cooldown_until_bar: float = 0.0
        self._cooldown_bar_origin: Optional[datetime] = None
        self._cooldown_reason: str = ""
        # 战术覆盖模式下, grid_engine.should_exit 不直接触发退出, 仅作为信号在这里累计.
        # last_engine_exit_signal: (reason, ts_iso) 用于 tactical_rules 后续可读取.
        # engine_exit_signal_count: 单 session 内累计触发次数 (诊断用).
        self.last_engine_exit_signal: Optional[tuple[str, str]] = None
        self.engine_exit_signal_count: int = 0

    # ──────────────────────────
    #  对外属性
    # ──────────────────────────

    @property
    def has_active_session(self) -> bool:
        return self.session is not None and self.session.status == "open"

    @property
    def in_cooldown(self) -> bool:
        return self.remaining_cooldown_bars() > 0

    def remaining_cooldown_bars(self) -> float:
        if self._cooldown_bar_origin is None or self._cooldown_until_bar <= 0:
            return 0.0
        try:
            strategy_hours = config.strategy_interval_hours()
            elapsed_bars = (
                (self.clock.now() - self._cooldown_bar_origin).total_seconds()
                / (strategy_hours * 3600)
            )
        except Exception:
            elapsed_bars = 0.0
        remaining = self._cooldown_until_bar - elapsed_bars
        return max(0.0, remaining)

    # ──────────────────────────
    #  生命周期 API
    # ──────────────────────────

    def start_session(self, symbol: str, start_equity: float, start_cash: float,
                      start_position: float, start_price: float,
                      confidence: str = tcfg.CONFIDENCE_NORMAL) -> SessionContext:
        if self.has_active_session:
            logger.warning(
                f"start_session 调用时已有 active session {self.session.session_id}, "
                "先收尾"
            )
            self.close_session(reason="auto_close_before_new_session",
                               end_price=start_price)

        now_iso = self.clock.now().isoformat()
        sid = f"sess_{self.clock.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        ctx = SessionContext(
            session_id=sid,
            symbol=symbol,
            started_at=now_iso,
            status="open",
            mode=tcfg.SESSION_MODE_OFFENSIVE,
            start_equity=float(start_equity or 0.0),
            start_cash=float(start_cash or 0.0),
            start_position=float(start_position or 0.0),
            start_price=float(start_price or 0.0),
            current_equity=float(start_equity or 0.0),
            current_cash=float(start_cash or 0.0),
            current_position=float(start_position or 0.0),
            current_price=float(start_price or 0.0),
            confidence=confidence,
            max_position_value=float(start_position or 0.0) * float(start_price or 0.0),
            created_at=now_iso,
            updated_at=now_iso,
        )
        self.session = ctx
        # 终止 cooldown 计时器
        self._cooldown_until_bar = 0.0
        self._cooldown_bar_origin = None
        self._cooldown_reason = ""
        # 新 session — 重置 engine_exit_signal 累计
        self.engine_exit_signal_count = 0
        self.last_engine_exit_signal = None

        self._persist_session_row()
        self._log_event("session_start",
                        details=(f"start_equity=${start_equity:.2f} "
                                 f"start_pos={start_position:.4f}@${start_price:.2f} "
                                 f"confidence={confidence}"),
                        action_taken="enter offensive grid")
        logger.info(
            f"🎯 Session 启动 {sid} | equity=${start_equity:.2f} "
            f"pos={start_position:.4f}@${start_price:.2f} confidence={confidence}"
        )
        return ctx

    def update_session(self, *, current_equity: float, current_cash: float,
                       current_position: float, current_price: float,
                       realized_pnl_delta: float = 0.0,
                       unrealized_pnl: Optional[float] = None) -> None:
        """主循环每个 step 调一次. realized_pnl_delta 是本次新增 (正卖出盈利, 负亏损)."""
        if not self.has_active_session:
            return
        s = self.session
        s.current_equity = float(current_equity or 0.0)
        s.current_cash = float(current_cash or 0.0)
        s.current_position = float(current_position or 0.0)
        s.current_price = float(current_price or 0.0)
        s.realized_pnl += float(realized_pnl_delta or 0.0)
        if unrealized_pnl is not None:
            s.unrealized_pnl = float(unrealized_pnl)
        s.total_pnl = s.realized_pnl + s.unrealized_pnl
        # peak / drawdown 跟踪
        if s.total_pnl > s.peak_pnl:
            s.peak_pnl = s.total_pnl
        drawdown = s.peak_pnl - s.total_pnl
        if drawdown > s.max_drawdown:
            s.max_drawdown = drawdown
        # max_position_value 跟踪 (用于报告 / 复盘)
        pv = s.current_position * s.current_price
        if pv > s.max_position_value:
            s.max_position_value = pv
        # age_bars
        try:
            started = datetime.fromisoformat(s.started_at)
            strategy_hours = config.strategy_interval_hours()
            s.age_bars = (
                (self.clock.now() - started).total_seconds() / (strategy_hours * 3600)
            )
        except Exception:
            pass

        s.updated_at = self.clock.now().isoformat()

    def evaluate_session(self, market: MarketContext) -> SessionEvaluation:
        """综合 tactical_rules 给出本步动作建议. 不修改 session.mode, 改由 caller 触发."""
        if not self.has_active_session:
            return SessionEvaluation()

        view = self.session.as_view()
        risk = rules.calculate_trend_risk_score(market)
        confidence = rules.calculate_confidence(market)
        # 把 confidence 更新回 session, 让 grid_bot 后续过滤 BUY 信号时直接读
        self.session.confidence = confidence

        # 1. 强制退出优先
        force_exit, reason = rules.should_force_exit(view, market, trend_risk_score=risk)
        if force_exit:
            return SessionEvaluation(
                action=ACTION_FORCE_EXIT, reason=reason,
                trend_risk_score=risk, confidence=confidence
            )

        # 2. 利润保护 (在 defensive 进入前判定: 一旦利润够大就锁利, 而不是先 DEFENSIVE 再亏回去)
        protect, p_reason, p_details = rules.should_protect_profit(view)
        if protect:
            action_code = p_details.get("action")
            if action_code == "exit":
                return SessionEvaluation(
                    action=ACTION_PROFIT_PROTECT_EXIT, reason=p_reason,
                    details=p_details, trend_risk_score=risk, confidence=confidence
                )
            if action_code == "partial_exit":
                return SessionEvaluation(
                    action=ACTION_PARTIAL_PROFIT_EXIT, reason=p_reason,
                    details=p_details, trend_risk_score=risk, confidence=confidence
                )

        # 3. DEFENSIVE 触发 (仅当当前还在 OFFENSIVE)
        if self.session.mode == tcfg.SESSION_MODE_OFFENSIVE:
            enter_def, def_reason = rules.should_enter_defensive(
                view, market, trend_risk_score=risk
            )
            if enter_def:
                return SessionEvaluation(
                    action=ACTION_ENTER_DEFENSIVE, reason=def_reason,
                    trend_risk_score=risk, confidence=confidence
                )

        return SessionEvaluation(
            action=ACTION_NONE, trend_risk_score=risk, confidence=confidence
        )

    def enter_defensive_mode(self, reason: str) -> None:
        if not self.has_active_session:
            return
        if self.session.mode == tcfg.SESSION_MODE_DEFENSIVE:
            return
        self.session.mode = tcfg.SESSION_MODE_DEFENSIVE
        self.session.defensive_reason = reason
        self._persist_session_row()
        self._log_event("enter_defensive", details=reason,
                        action_taken="cancel buys, sell-only")
        logger.warning(
            f"🛡️  Session {self.session.session_id} OFFENSIVE → DEFENSIVE | {reason}"
        )

    def mark_exiting(self, reason: str) -> None:
        """记录 session 进入 EXIT 状态; close_session 在清仓真正完成后被 grid_bot 调."""
        if not self.has_active_session:
            return
        self.session.mode = tcfg.SESSION_MODE_EXITING
        self.session.exit_reason = reason or self.session.exit_reason
        self._persist_session_row()
        self._log_event("mark_exiting", details=reason,
                        action_taken="prepare EXIT_PENDING")
        logger.warning(
            f"⏏️  Session {self.session.session_id} → EXITING | {reason}"
        )

    def record_recenter(self, reason: str = "") -> None:
        """grid_bot 在 recenter 完成后调, 让 SessionManager 知道 +1.

        S1 用这个计数决定"首次零成交追价"是否还能用 (recenter_used_count == 0
        才放行 rescue recenter)."""
        if not self.has_active_session:
            return
        self.session.recenter_used_count += 1
        self._persist_session_row()
        self._log_event(
            "recenter",
            details=str(reason or ""),
            action_taken=f"recenter_used={self.session.recenter_used_count}",
        )

    def record_engine_exit_signal(self, reason: str,
                                   current_time: Optional[datetime] = None) -> None:
        """战术覆盖模式下接收 grid_engine.should_exit 信号 (不触发 EXIT_PENDING).

        作用:
          - 累计 engine_exit_signal_count, 暴露给单测验证 / 后续 tactical_rules 读取
          - 写一条 grid_session_events.event_type='engine_exit_signal' 便于事后回放
          - 不修改 session.mode / state_machine.state
        """
        if not self.has_active_session:
            return
        ts = current_time or self.clock.now()
        try:
            ts_iso = ts.isoformat()
        except AttributeError:
            ts_iso = str(ts)
        self.engine_exit_signal_count += 1
        self.last_engine_exit_signal = (str(reason or ""), ts_iso)
        self._log_event(
            "engine_exit_signal",
            details=str(reason or ""),
            action_taken=f"input_only count={self.engine_exit_signal_count}",
        )

    def record_buy_fill(self, level_index: int, quantity: float, price: float,
                        commission: float) -> None:
        """grid_bot 在 BUY 成交后通知 session, 用于:
            - filled_buy_levels 更新 (rules 用)
            - max_position_value tracking (update_session 也会做, 这里冗余无害)
        """
        if not self.has_active_session:
            return
        if level_index < 0 and level_index not in self.session.filled_buy_levels:
            self.session.filled_buy_levels.append(level_index)

    def record_sell_fill(self, level_index: int, quantity: float, price: float,
                         net_pnl: float) -> None:
        if not self.has_active_session:
            return
        # net_pnl 已经在 grid_bot 调 pnl_tracker.record_sell 后拿到, 这里只更新 session 视图.
        # realized_pnl_delta 在 update_session 中体现; 这里仅日志.
        self._log_event(
            "sell_fill",
            details=f"level={level_index} qty={quantity:.4f}@${price:.2f} pnl=${net_pnl:+.2f}",
            action_taken="grid sell"
        )

    def close_session(self, reason: str, end_price: float = None) -> Optional[SessionContext]:
        if not self.has_active_session:
            return None
        s = self.session
        now_iso = self.clock.now().isoformat()
        s.status = "closed"
        s.ended_at = now_iso
        s.exit_reason = (reason or s.exit_reason or "closed")
        # 收尾快照
        s.end_equity = s.current_equity
        s.end_cash = s.current_cash
        s.end_position = s.current_position
        s.end_price = float(end_price) if end_price is not None else s.current_price
        s.updated_at = now_iso

        self._persist_session_row()
        self._log_event("session_close",
                        details=(f"total_pnl=${s.total_pnl:+.2f} "
                                 f"peak=${s.peak_pnl:+.2f} mdd=${s.max_drawdown:.2f} "
                                 f"age={s.age_bars:.1f}bars"),
                        action_taken=reason)
        logger.info(
            f"🏁 Session {s.session_id} 关闭 | reason={reason} "
            f"pnl=${s.total_pnl:+.2f} peak=${s.peak_pnl:+.2f} age={s.age_bars:.1f}bars"
        )
        closed = s
        self.session = None
        return closed

    # ──────────────────────────
    #  Cooldown
    # ──────────────────────────

    def start_cooldown(self, bars: float, reason: str) -> None:
        if bars <= 0:
            self._cooldown_until_bar = 0.0
            self._cooldown_bar_origin = None
            self._cooldown_reason = ""
            return
        self._cooldown_until_bar = float(bars)
        self._cooldown_bar_origin = self.clock.now()
        self._cooldown_reason = reason
        logger.info(f"🧊 进入 COOLDOWN {bars:.1f}bars | {reason}")

    def clear_cooldown(self) -> None:
        self._cooldown_until_bar = 0.0
        self._cooldown_bar_origin = None
        self._cooldown_reason = ""

    # ──────────────────────────
    #  持久化
    # ──────────────────────────

    def _persist_session_row(self) -> None:
        if not self.session:
            return
        s = self.session
        # 确保表存在 (启动迁移会建; 这里再 ensure 一次保证测试场景下也安全)
        try:
            self.db.ensure_grid_sessions_table()
        except Exception:
            pass
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO grid_sessions
                       (session_id, symbol, started_at, ended_at, status, mode,
                        start_equity, end_equity, start_cash, end_cash,
                        start_price, end_price, start_position, end_position,
                        realized_pnl, unrealized_pnl, total_pnl, peak_pnl,
                        max_drawdown, max_position_value, age_bars,
                        exit_reason, defensive_reason, confidence,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (s.session_id, s.symbol, s.started_at, s.ended_at, s.status, s.mode,
                     s.start_equity, s.end_equity, s.start_cash, s.end_cash,
                     s.start_price, s.end_price, s.start_position, s.end_position,
                     s.realized_pnl, s.unrealized_pnl, s.total_pnl, s.peak_pnl,
                     s.max_drawdown, s.max_position_value, s.age_bars,
                     s.exit_reason, s.defensive_reason, s.confidence,
                     s.created_at, s.updated_at)
                )
        except Exception as e:
            logger.warning(f"_persist_session_row 失败 (非致命): {e}")

    def _log_event(self, event_type: str, details: str = "",
                   action_taken: str = "") -> None:
        if not self.session:
            return
        try:
            log = getattr(self.db, "log_session_event", None)
            if callable(log):
                log(self.session.session_id, event_type, details, action_taken)
        except Exception as e:
            logger.warning(f"_log_event 失败 (非致命): {e}")

    # ──────────────────────────
    #  恢复 (启动时调用)
    # ──────────────────────────

    def load_active_session(self) -> Optional[SessionContext]:
        """启动时从 grid_sessions 表恢复最新一个 status='open' 的 session.
        没有则返回 None — caller 自行决定是否新开."""
        try:
            self.db.ensure_grid_sessions_table()
            with sqlite3.connect(self.db.db_path) as conn:
                row = conn.execute(
                    """SELECT session_id, symbol, started_at, ended_at, status, mode,
                              start_equity, end_equity, start_cash, end_cash,
                              start_price, end_price, start_position, end_position,
                              realized_pnl, unrealized_pnl, total_pnl, peak_pnl,
                              max_drawdown, max_position_value, age_bars,
                              exit_reason, defensive_reason, confidence,
                              created_at, updated_at
                       FROM grid_sessions WHERE status='open'
                       ORDER BY started_at DESC LIMIT 1"""
                ).fetchone()
        except Exception as e:
            logger.warning(f"load_active_session 失败 (非致命): {e}")
            return None
        if not row:
            return None
        ctx = SessionContext(
            session_id=row[0], symbol=row[1] or "",
            started_at=row[2] or "", ended_at=row[3],
            status=row[4] or "open", mode=row[5] or tcfg.SESSION_MODE_OFFENSIVE,
            start_equity=row[6] or 0.0, end_equity=row[7],
            start_cash=row[8] or 0.0, end_cash=row[9],
            start_price=row[10] or 0.0, end_price=row[11],
            start_position=row[12] or 0.0, end_position=row[13],
            realized_pnl=row[14] or 0.0, unrealized_pnl=row[15] or 0.0,
            total_pnl=row[16] or 0.0, peak_pnl=row[17] or 0.0,
            max_drawdown=row[18] or 0.0, max_position_value=row[19] or 0.0,
            age_bars=row[20] or 0.0,
            exit_reason=row[21] or "", defensive_reason=row[22] or "",
            confidence=row[23] or tcfg.CONFIDENCE_NORMAL,
            created_at=row[24] or "", updated_at=row[25] or "",
        )
        ctx.current_equity = ctx.start_equity
        ctx.current_cash = ctx.start_cash
        ctx.current_position = ctx.start_position
        ctx.current_price = ctx.start_price
        self.session = ctx
        logger.info(
            f"📂 Session 恢复 {ctx.session_id} | mode={ctx.mode} "
            f"start_equity=${ctx.start_equity:.2f} age≈{ctx.age_bars:.1f}bars"
        )
        return ctx
