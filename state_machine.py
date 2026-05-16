"""
state_machine.py — 系统状态机 (v3, 战术网格扩展)

六种状态 (v3 起):
  1. SCANNING        — 扫描中, 等待入场条件满足
  2. WAITING_ENTRY   — 条件满足, 等待理想入场价
  3. OFFENSIVE_GRID  — 进攻型网格运行中 (允许买/卖)
  4. DEFENSIVE_GRID  — 防守型网格 (停止补仓, 只允许卖出 / 反弹减仓)
  5. EXIT_PENDING    — 退出中 (已撤单, 等待持仓清理)
  6. COOLDOWN        — 上一轮退出后冷却, 暂不开新 session

向后兼容:
  - 保留 SystemState.ACTIVE_GRID = "active_grid" 作为旧 DB 行的合法值,
    load_state 时透明翻译成 OFFENSIVE_GRID. 新代码不再产出 "active_grid" 字符串.

状态转换由 evaluate() 方法驱动, 外部循环每次调用即可.

持久化:
  save_state(db_path) / load_state(db_path) 把 StateContext 落盘到 state_machine_state 表,
  实现重启后状态机状态的连续性. 旧 DB schema (没有新字段) 完全兼容; 新加字段都是 nullable.
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
    # 战术网格三态
    OFFENSIVE_GRID = "offensive_grid"
    DEFENSIVE_GRID = "defensive_grid"
    COOLDOWN = "cooldown"
    EXIT_PENDING = "exit_pending"
    # 旧版兼容: 仅供 enum lookup 与 import 兼容, 新代码不应再创建此值.
    # load_state 会把 DB 中的 "active_grid" 翻译成 OFFENSIVE_GRID.
    ACTIVE_GRID = "active_grid"


# 所有 "网格存在 + 主循环要走网格处理" 的状态.
# EXIT_PENDING 不算在内 — 它有专门的清仓 handler.
GRID_MODE_STATES = frozenset({
    SystemState.OFFENSIVE_GRID,
    SystemState.DEFENSIVE_GRID,
    SystemState.ACTIVE_GRID,  # 旧值, 防御性留着 — 实践中 load_state 已翻译过.
})


def is_grid_state(state: SystemState) -> bool:
    """`state` 是否处于"网格已建仓且仍在运行"阶段."""
    return state in GRID_MODE_STATES


def is_position_holding_state(state: SystemState) -> bool:
    """`state` 是否还在持仓 (含 EXIT_PENDING). SCANNING/WAITING_ENTRY/COOLDOWN 视为空仓."""
    return state in GRID_MODE_STATES or state == SystemState.EXIT_PENDING


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

    def __init__(self, clock=None):
        # clock 注入 — 与 PnLTracker / RiskManager 一致: 测试 / 回测传 HistoricalClock,
        # 实盘默认 LiveClock. 业务模块禁止直接读 wall-clock.
        if clock is None:
            from interfaces import LiveClock
            clock = LiveClock()
        self._clock = clock
        self.context = StateContext(
            current_state=SystemState.SCANNING,
            state_entered_at=self._clock.now().isoformat(),
        )

    @property
    def state(self) -> SystemState:
        return self.context.current_state

    def transition_to(self, new_state: SystemState, reason: str = "",
                      now: Optional[datetime] = None):
        """状态转换"""
        now = now or self._clock.now()
        old = self.context.current_state
        self.context.current_state = new_state
        self.context.state_entered_at = now.isoformat()

        logger.info(f"🔄 状态转换: {old.value} → {new_state.value}"
                    + (f" ({reason})" if reason else ""))

        # 状态进入时的初始化
        if new_state == SystemState.WAITING_ENTRY:
            self.context.entry_window_started_at = now.isoformat()
        elif new_state in (SystemState.OFFENSIVE_GRID, SystemState.ACTIVE_GRID):
            # 第一次从 WAITING_ENTRY 切到 grid 才计 session;
            # OFFENSIVE ↔ DEFENSIVE 来回切换不重复计.
            if old in (SystemState.WAITING_ENTRY, SystemState.SCANNING):
                self.context.grid_active_since = now.isoformat()
                self.context.total_grid_sessions += 1
            self.context.entry_window_started_at = None
        elif new_state == SystemState.DEFENSIVE_GRID:
            # 来自 OFFENSIVE → DEFENSIVE 仍属于同一 session, 不递增 total_grid_sessions.
            # 仅记录 entered_at; grid_active_since 保留 (用于 age 计算).
            pass
        elif new_state == SystemState.EXIT_PENDING:
            self.context.exit_initiated_at = now.isoformat()
            self.context.exit_reason = reason
            self.context.total_exits += 1
        elif new_state == SystemState.COOLDOWN:
            # 进入 COOLDOWN: 清掉 grid 上下文, 但保留 exit_reason 供日志/报告
            self.context.grid_active_since = None
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
        now = now or self._clock.now()
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
        """检查WAITING_ENTRY是否超时.

        引入 ENTRY_TIMEOUT_EPSILON_BARS 吸收浮点 / 调度漂移:
        bar 边界处 (elapsed≈ENTRY_MAX_WAIT_BARS) 不应被判超时,
        防止 WAITING_ENTRY 在第一次重新评估 timing 前就被消耗.
        """
        if self.state != SystemState.WAITING_ENTRY:
            return False
        if not self.context.entry_window_started_at:
            return False
        current_time = current_time or self._clock.now()
        started = datetime.fromisoformat(self.context.entry_window_started_at)
        strategy_hours = config.strategy_interval_hours()
        elapsed_bars = (current_time - started).total_seconds() / (strategy_hours * 3600)
        epsilon = float(getattr(config, "ENTRY_TIMEOUT_EPSILON_BARS", 1e-6))
        if elapsed_bars > config.ENTRY_MAX_WAIT_BARS + epsilon:
            self.transition_to(SystemState.SCANNING,
                               f"等待入场超时 ({elapsed_bars:.2f} bars)",
                               now=current_time)
            return True
        return False

    def on_grid_active(self, now: Optional[datetime] = None):
        """从 WAITING_ENTRY → OFFENSIVE_GRID (建仓成功后调用).

        v3 起目标态是 OFFENSIVE_GRID. 旧调用方代码无需改动 — 这里 dispatch 即可."""
        if self.state == SystemState.WAITING_ENTRY:
            self.transition_to(SystemState.OFFENSIVE_GRID, "建仓完成", now=now)

    def on_enter_defensive(self, reason: str,
                           now: Optional[datetime] = None):
        """OFFENSIVE_GRID → DEFENSIVE_GRID."""
        if self.state in (SystemState.OFFENSIVE_GRID, SystemState.ACTIVE_GRID):
            self.transition_to(SystemState.DEFENSIVE_GRID, reason, now=now)

    def on_back_to_offensive(self, reason: str = "",
                             now: Optional[datetime] = None):
        """DEFENSIVE_GRID → OFFENSIVE_GRID (例如反弹回到 EMA 之上, 风险评分回落).
        当前业务保守: 一旦 DEFENSIVE 就只允许 EXIT, 不主动回 OFFENSIVE.
        保留 API 供未来策略迭代."""
        if self.state == SystemState.DEFENSIVE_GRID:
            self.transition_to(SystemState.OFFENSIVE_GRID, reason or "回到 OFFENSIVE", now=now)

    def on_exit_signal(self, should_exit: bool, reason: str = "",
                       now: Optional[datetime] = None):
        """处理退出信号 — OFFENSIVE / DEFENSIVE / 旧 ACTIVE_GRID 都可触发."""
        if self.state not in (SystemState.OFFENSIVE_GRID, SystemState.DEFENSIVE_GRID,
                              SystemState.ACTIVE_GRID):
            return None
        if should_exit:
            self.transition_to(SystemState.EXIT_PENDING, reason, now=now)
            return "INITIATE_EXIT"
        return None

    def on_exit_complete(self, now: Optional[datetime] = None,
                         enter_cooldown_bars: float = 0.0,
                         cooldown_reason: str = ""):
        """退出完成 (持仓清理完毕).

        enter_cooldown_bars > 0 时: EXIT_PENDING → COOLDOWN, grid_bot 后续根据
        session_manager.in_cooldown 决定何时回 SCANNING.
        否则按旧语义直接回 SCANNING.
        """
        if self.state != SystemState.EXIT_PENDING:
            return
        if enter_cooldown_bars > 0:
            self.transition_to(
                SystemState.COOLDOWN,
                f"退出完成, 进入 cooldown {enter_cooldown_bars:.1f}bars" +
                (f" ({cooldown_reason})" if cooldown_reason else ""),
                now=now
            )
        else:
            self.transition_to(SystemState.SCANNING, "退出完成, 重新扫描", now=now)

    def on_cooldown_complete(self, now: Optional[datetime] = None):
        """COOLDOWN → SCANNING. 由 grid_bot 看 session_manager.remaining_cooldown_bars 判断."""
        if self.state == SystemState.COOLDOWN:
            self.transition_to(SystemState.SCANNING, "cooldown 结束", now=now)

    def on_recenter(self, now: Optional[datetime] = None):
        """记录中轴重置"""
        now = now or self._clock.now()
        self.context.last_recenter_at = now.isoformat()
        self.context.total_recenters += 1

    # ──────────────────────────────
    #  查询方法
    # ──────────────────────────────

    def get_check_interval_sec(self) -> int:
        """根据当前状态返回应该的检查频率 — SCANNING/WAITING 动态随 STRATEGY_INTERVAL.

        OFFENSIVE/DEFENSIVE 共用 ACTIVE_CHECK_INTERVAL_SEC (1 分钟级), 这样
        DEFENSIVE 仍可及时捕捉反弹卖出机会. COOLDOWN 与 SCANNING 同步, 按
        策略周期 sleep — 没必要每分钟检查.
        """
        return {
            SystemState.SCANNING: config.scanning_interval_sec(),
            SystemState.WAITING_ENTRY: config.waiting_interval_sec(),
            SystemState.OFFENSIVE_GRID: config.ACTIVE_CHECK_INTERVAL_SEC,
            SystemState.DEFENSIVE_GRID: config.ACTIVE_CHECK_INTERVAL_SEC,
            SystemState.ACTIVE_GRID: config.ACTIVE_CHECK_INTERVAL_SEC,
            SystemState.EXIT_PENDING: config.ACTIVE_CHECK_INTERVAL_SEC,
            SystemState.COOLDOWN: config.scanning_interval_sec(),
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
                 self._clock.now().isoformat())
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
            raw_state = row[0]
            # 向后兼容: 旧 DB 中 "active_grid" 翻译成 OFFENSIVE_GRID.
            # SystemState 仍保留 ACTIVE_GRID 字面值, 这里显式映射避免运行期
            # 出现两个具有相同语义的 enum 流转, 让后续业务只看 OFFENSIVE/DEFENSIVE.
            if raw_state == SystemState.ACTIVE_GRID.value:
                logger.info(
                    "  ↪︎ 旧 active_grid 状态已翻译为 offensive_grid (兼容)"
                )
                restored_state = SystemState.OFFENSIVE_GRID
            else:
                restored_state = SystemState(raw_state)

            self.context = StateContext(
                current_state=restored_state,
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
        in_state_for = self._clock.now() - entered
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
