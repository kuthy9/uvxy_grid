"""Bug 3 fix: _try_restore_grid 拆 EXIT_PENDING 分支.

Background: 旧实现把 EXIT_PENDING 和 grid states (OFFENSIVE/DEFENSIVE/ACTIVE)
合并处理: 任一状态下缺 .grid.json 都强制回退 SCANNING.

但 EXIT_PENDING 的 _handle_exit_pending 只用持仓 + FIFO 队列, 完全不读
grid 快照. LEGACY 持仓被 reconcile drift #1 拉到 EXIT_PENDING 时, FIFO
本来就是空的, .grid.json 也不存在. 旧实现绕一圈:
    EXIT_PENDING → (缺 .grid.json) → SCANNING → reconcile drift #1 → EXIT_PENDING
状态写两次, 日志噪声.

修复 contract:
  * EXIT_PENDING + 缺 .grid.json → 保持 EXIT_PENDING, grid=None
  * EXIT_PENDING + 有 .grid.json → 加载 grid (用于显示)
  * EXIT_PENDING + .grid.json 损坏 → 保持 EXIT_PENDING, grid=None (不回退)
  * grid_state + 缺 .grid.json → 仍回退 SCANNING (旧行为保留)
  * grid_state + 有 .grid.json → 正常加载
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from grid_bot import GridBot, _grid_state_path
from state_machine import StateMachine, SystemState
from interfaces import HistoricalClock


class _RestoreFixture(unittest.TestCase):
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
        self.clock.set(datetime(2026, 5, 18, 11, 0))
        self.executor = MagicMock()

        self.bot = GridBot(
            clock=self.clock, executor=self.executor, db=self.db,
            pnl=PnLTracker(db_path=self.db_path, clock=self.clock),
            risk=RiskManager(self.db, clock=self.clock),
            state_machine=StateMachine(),
            entry_filter=EntryFilter(),
            data_fetcher=None,
        )

    def tearDown(self):
        from grid_bot import _grid_state_path, _base_shares_path
        for p in (_grid_state_path(self.db_path),
                  _base_shares_path(self.db_path),
                  self.tmp.name):
            if os.path.exists(p):
                os.unlink(p)

    def _set_state(self, state: SystemState) -> None:
        self.bot.state_machine.context.current_state = state

    def _make_corrupt_grid_file(self) -> str:
        path = _grid_state_path(self.db_path)
        with open(path, "w") as f:
            f.write("{not valid json")
        return path

    def _make_valid_grid_file(self) -> str:
        from grid_engine import DynamicGridEngine
        g = DynamicGridEngine(
            center_price=35.0, atr=1.2, grid_capital=500.0,
            current_time=datetime(2026, 5, 18, 10, 0),
        )
        path = _grid_state_path(self.db_path)
        g.save_state(path)
        return path


class TestExitPendingMissingGridSnapshot(_RestoreFixture):

    def test_exit_pending_missing_grid_stays_exit_pending(self):
        """关键场景: 旧实现把这条路径绕到 SCANNING, 修复后保持 EXIT_PENDING."""
        self._set_state(SystemState.EXIT_PENDING)
        assert not os.path.exists(_grid_state_path(self.db_path))

        self.bot._try_restore_grid()

        assert self.bot.state_machine.state == SystemState.EXIT_PENDING, (
            "EXIT_PENDING + 缺 grid 快照应保持原状态, 不应回退 SCANNING. "
            f"实际: {self.bot.state_machine.state.value}"
        )
        assert self.bot.grid is None, "缺快照时 grid 应保持 None"

    def test_exit_pending_corrupt_grid_stays_exit_pending(self):
        """快照损坏也不应回退 — 不依赖 grid 数据就能清仓."""
        self._set_state(SystemState.EXIT_PENDING)
        self._make_corrupt_grid_file()

        self.bot._try_restore_grid()

        assert self.bot.state_machine.state == SystemState.EXIT_PENDING
        assert self.bot.grid is None

    def test_exit_pending_with_valid_snapshot_loads_for_context(self):
        """有快照时仍加载 (供 display / 调试), 状态保持."""
        self._set_state(SystemState.EXIT_PENDING)
        self._make_valid_grid_file()

        self.bot._try_restore_grid()

        assert self.bot.state_machine.state == SystemState.EXIT_PENDING
        assert self.bot.grid is not None
        assert abs(self.bot.grid.center_price - 35.0) < 1e-6


class TestGridStatesRegressionPreserved(_RestoreFixture):
    """回归保护: grid states (ACTIVE_GRID/OFFENSIVE_GRID/DEFENSIVE_GRID)
    缺快照时仍应回退 SCANNING (这是 grid 状态机的契约 — grid 数据丢失就不能
    继续按原网格执行交易, 必须降级).
    """

    def test_active_grid_missing_snapshot_falls_back_to_scanning(self):
        self._set_state(SystemState.ACTIVE_GRID)
        assert not os.path.exists(_grid_state_path(self.db_path))

        self.bot._try_restore_grid()

        assert self.bot.state_machine.state == SystemState.SCANNING, (
            "ACTIVE_GRID + 缺 grid 快照必须回退 SCANNING — 这是数据完整性要求, "
            "不能继续按已失效的网格交易"
        )

    def test_active_grid_corrupt_snapshot_falls_back_to_scanning(self):
        self._set_state(SystemState.ACTIVE_GRID)
        self._make_corrupt_grid_file()

        self.bot._try_restore_grid()

        assert self.bot.state_machine.state == SystemState.SCANNING


class TestNonGridStatesNoOp(_RestoreFixture):
    """SCANNING / WAITING_ENTRY 等无 grid 期望的状态: 无论快照是否存在,
    _try_restore_grid 都是 no-op.
    """

    def test_scanning_does_not_load_grid(self):
        self._set_state(SystemState.SCANNING)
        self._make_valid_grid_file()  # 即使有快照也不该被恢复
        self.bot._try_restore_grid()
        assert self.bot.state_machine.state == SystemState.SCANNING
        assert self.bot.grid is None

    def test_waiting_entry_does_not_load_grid(self):
        self._set_state(SystemState.WAITING_ENTRY)
        self._make_valid_grid_file()
        self.bot._try_restore_grid()
        assert self.bot.state_machine.state == SystemState.WAITING_ENTRY
        assert self.bot.grid is None


if __name__ == "__main__":
    unittest.main()
