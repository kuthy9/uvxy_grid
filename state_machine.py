"""
state_machine.py — 系统状态机

四种状态:
  1. SCANNING       — 扫描中，等待入场条件满足
  2. WAITING_ENTRY  — 条件满足，等待理想入场价
  3. ACTIVE_GRID    — 网格运行中
  4. EXIT_PENDING   — 退出中(已撤单，等待持仓自然减少)

状态转换由 evaluate() 方法驱动，外部循环每次调用即可。

持久化:
  save_state(db_path) / load_state(db_path) 把 StateContext 落盘到 state_machine_state 表,
  实现重启后状态机状态的连续性。
"""

import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import config

logger = logging.getLogger("GridTrader.StateMachine")


class SystemState(Enum):
    SCANNING = "scanning"
    WAITING_ENTRY = "waiting_entry"
    ACTIVE_GRID = "active_grid"
    EXIT_PENDING = "exit_pending"


@dataclass
class StateContext:
    """状态机上下文（持久化的状态信息）"""
    current_state: SystemState = SystemState.SCANNING
    state_entered_at: str = ""

    # 入场相关
    last_evaluation_time: Optional[str] = None
    entry_window_started_at: Optional[str] = None  # 进入WAITING_ENTRY的时间

    # 网格相关
    grid_active_since: Optional[str] = None
    last_recenter_at: Optional[str] = None

    # 退出相关
    exit_initiated_at: Optional[str] = None
    exit_reason: str = ""

    # 统计
    total_grid_sessions: int = 0
    total_recenters: int = 0
    total_exits: int = 0


class StateMachine:
    """
    主状态机
    
    使用方式:
        sm = StateMachine()
        action = sm.evaluate(market_data)  # 返回需要执行的动作
        sm.commit_transition(action)        # 确认动作执行后转换状态
    """

    def __init__(self):
        self.context = StateContext(
            current_state=SystemState.SCANNING,
            state_entered_at=datetime.now().isoformat(),
        )

    @property
    def state(self) -> SystemState:
        return self.context.current_state

    def transition_to(self, new_state: SystemState, reason: str = "",
                      now: Optional[datetime] = None):
        """状态转换"""
        now = now or datetime.now()
        old = self.context.current_state
        self.context.current_state = new_state
        self.context.state_entered_at = now.isoformat()

        logger.info(f"🔄 状态转换: {old.value} → {new_state.value}"
                    + (f" ({reason})" if reason else ""))

        # 状态进入时的初始化
        if new_state == SystemState.WAITING_ENTRY:
            self.context.entry_window_started_at = now.isoformat()
        elif new_state == SystemState.ACTIVE_GRID:
            self.context.grid_active_since = now.isoformat()
            self.context.total_grid_sessions += 1
            self.context.entry_window_started_at = None
        elif new_state == SystemState.EXIT_PENDING:
            self.context.exit_initiated_at = now.isoformat()
            self.context.exit_reason = reason
            self.context.total_exits += 1
        elif new_state == SystemState.SCANNING:
            # 重置所有临时状态
            self.context.entry_window_started_at = None
            self.context.grid_active_since = None
            self.context.exit_initiated_at = None
            self.context.exit_reason = ""

    # ──────────────────────────────
    #  状态转换决策方法
    # ──────────────────────────────

    def on_entry_evaluation(self, allow_entry: bool, reason: str = "",
                            now: Optional[datetime] = None):
        """处理入场筛选器的评估结果"""
        now = now or datetime.now()
        self.context.last_evaluation_time = now.isoformat()

        if self.state == SystemState.SCANNING:
            if allow_entry:
                self.transition_to(SystemState.WAITING_ENTRY, "条件满足", now=now)
                return "ENTER_WAITING"
            return "STAY_SCANNING"

        if self.state == SystemState.WAITING_ENTRY:
            if allow_entry:
                # 仍然合适，可以建仓
                return "EXECUTE_ENTRY"
            else:
                # 条件变差，回到扫描
                self.transition_to(SystemState.SCANNING, f"条件失效: {reason}", now=now)
                return "BACK_TO_SCANNING"

        return None

    def check_entry_timeout(self, current_time: Optional[datetime] = None) -> bool:
        """检查WAITING_ENTRY是否超时"""
        if self.state != SystemState.WAITING_ENTRY:
            return False
        if not self.context.entry_window_started_at:
            return False
        current_time = current_time or datetime.now()
        started = datetime.fromisoformat(self.context.entry_window_started_at)
        strategy_hours = config.strategy_interval_hours()
        elapsed_bars = (current_time - started).total_seconds() / (strategy_hours * 3600)
        if elapsed_bars > config.ENTRY_MAX_WAIT_BARS:
            self.transition_to(SystemState.SCANNING,
                               f"等待入场超时 ({elapsed_bars:.1f} bars)",
                               now=current_time)
            return True
        return False

    def on_grid_active(self, now: Optional[datetime] = None):
        """从WAITING_ENTRY → ACTIVE_GRID (建仓成功后调用)"""
        if self.state == SystemState.WAITING_ENTRY:
            self.transition_to(SystemState.ACTIVE_GRID, "建仓完成", now=now)

    def on_exit_signal(self, should_exit: bool, reason: str = "",
                       now: Optional[datetime] = None):
        """处理退出信号"""
        if self.state != SystemState.ACTIVE_GRID:
            return None
        if should_exit:
            self.transition_to(SystemState.EXIT_PENDING, reason, now=now)
            return "INITIATE_EXIT"
        return None

    def on_exit_complete(self, now: Optional[datetime] = None):
        """退出完成（持仓清理完毕）"""
        if self.state == SystemState.EXIT_PENDING:
            self.transition_to(SystemState.SCANNING, "退出完成，重新扫描", now=now)

    def on_recenter(self, now: Optional[datetime] = None):
        """记录中轴重置"""
        now = now or datetime.now()
        self.context.last_recenter_at = now.isoformat()
        self.context.total_recenters += 1

    # ──────────────────────────────
    #  查询方法
    # ──────────────────────────────

    def get_check_interval_sec(self) -> int:
        """根据当前状态返回应该的检查频率 — SCANNING/WAITING 动态随 STRATEGY_INTERVAL"""
        return {
            SystemState.SCANNING: config.scanning_interval_sec(),
            SystemState.WAITING_ENTRY: config.waiting_interval_sec(),
            SystemState.ACTIVE_GRID: config.ACTIVE_CHECK_INTERVAL_SEC,
            SystemState.EXIT_PENDING: config.ACTIVE_CHECK_INTERVAL_SEC,
        }.get(self.state, 60)

    # ──────────────────────────────
    #  持久化
    # ──────────────────────────────

    def _ensure_table(self, db_path: str):
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS state_machine_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_state TEXT NOT NULL,
                    state_entered_at TEXT,
                    last_evaluation_time TEXT,
                    entry_window_started_at TEXT,
                    grid_active_since TEXT,
                    last_recenter_at TEXT,
                    exit_initiated_at TEXT,
                    exit_reason TEXT,
                    total_grid_sessions INTEGER DEFAULT 0,
                    total_recenters INTEGER DEFAULT 0,
                    total_exits INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            """)

    def save_state(self, db_path: str):
        """把当前状态落盘. 单行覆盖写 (id=1)."""
        self._ensure_table(db_path)
        ctx = self.context
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO state_machine_state
                   (id, current_state, state_entered_at, last_evaluation_time,
                    entry_window_started_at, grid_active_since, last_recenter_at,
                    exit_initiated_at, exit_reason,
                    total_grid_sessions, total_recenters, total_exits, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ctx.current_state.value, ctx.state_entered_at,
                 ctx.last_evaluation_time, ctx.entry_window_started_at,
                 ctx.grid_active_since, ctx.last_recenter_at,
                 ctx.exit_initiated_at, ctx.exit_reason,
                 ctx.total_grid_sessions, ctx.total_recenters, ctx.total_exits,
                 datetime.now().isoformat())
            )

    def load_state(self, db_path: str) -> bool:
        """从数据库恢复状态. 返回是否成功."""
        self._ensure_table(db_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """SELECT current_state, state_entered_at, last_evaluation_time,
                          entry_window_started_at, grid_active_since, last_recenter_at,
                          exit_initiated_at, exit_reason,
                          total_grid_sessions, total_recenters, total_exits
                   FROM state_machine_state WHERE id = 1"""
            ).fetchone()
        if not row:
            return False
        try:
            self.context = StateContext(
                current_state=SystemState(row[0]),
                state_entered_at=row[1] or "",
                last_evaluation_time=row[2],
                entry_window_started_at=row[3],
                grid_active_since=row[4],
                last_recenter_at=row[5],
                exit_initiated_at=row[6],
                exit_reason=row[7] or "",
                total_grid_sessions=row[8] or 0,
                total_recenters=row[9] or 0,
                total_exits=row[10] or 0,
            )
            logger.info(f"📂 StateMachine 恢复: state={self.context.current_state.value} "
                        f"| sessions={self.context.total_grid_sessions}")
            return True
        except Exception as e:
            logger.error(f"StateMachine 恢复失败: {e}, 回退到默认 SCANNING")
            return False

    # ──────────────────────────────
    #  查询方法
    # ──────────────────────────────

    def get_status_summary(self) -> str:
        """状态摘要(用于日志和报告)"""
        ctx = self.context
        entered = datetime.fromisoformat(ctx.state_entered_at)
        in_state_for = datetime.now() - entered
        if config.STRATEGY_INTERVAL.endswith("d"):
            duration_text = f"{in_state_for.total_seconds() / 86400:.1f} 天"
        else:
            duration_text = f"{in_state_for.total_seconds() / 3600:.1f} 小时"

        lines = [
            f"  状态: {ctx.current_state.value.upper()}",
            f"  在此状态: {duration_text}",
            f"  累计: {ctx.total_grid_sessions} 次网格 / "
            f"{ctx.total_recenters} 次重置 / {ctx.total_exits} 次退出",
        ]
        if ctx.exit_reason and self.state == SystemState.EXIT_PENDING:
            lines.append(f"  退出原因: {ctx.exit_reason}")
        return "\n".join(lines)
