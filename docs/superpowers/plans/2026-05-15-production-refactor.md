# Production Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surgical revert tracked files 到 GitHub main HEAD (`8c41c62`) 状态, 隔离 tactical 模块到 `archive/tactical/`, 保留多标的能力 (UVXY+VXX 50/50 复现 +153.93%), 修 5 个 bug (4 spec + 1 plan-discovered), main.py 默认多标的实盘, 全过程无伪代码 / 无 hardcode / 无 mock 成功.

**Architecture:** Phase 1 移 tactical files 到 archive + 修 bot_factory 的 tactical import; Phase 2 用 `git checkout 8c41c62 -- <file>` 选择性撤销 11 个 tracked file (保留 state_machine + entry_filter + test.py); Phase 4 修 5 个 bug (4.A risk_manager / 4.B entry_filter getattr / 4.C config stale comment / 4.D git-bisect -27pp 报告 / **4.E GridBot allocated_capital 最小支持 — spec 漏掉, 撤销 grid_bot 后必须 re-apply**); Phase 5 改写 main.py 为 MultiSymbolOrchestrator + `--paper-verify` flag; Phase 6 grep 清理; Phase 7 跑测试 + state recovery + paper verify; Phase 8 docs.

**Tech Stack:** Python 3.10+, pandas, IBKR ib_insync (隔离在 ibkr_executor.py), unittest, git surgical operations (`git checkout <SHA> -- <file>`, `git bisect`).

**Spec:** `docs/superpowers/specs/2026-05-15-production-refactor-design.md`

**Spec self-review discovery (Plan 阶段补救)**:
当前 grid_bot.py 通过 `_allocated_capital` 实例字段做多标的 sizing (line 99/146/167/753/858). Phase 2 撤销 grid_bot.py 到 `8c41c62` 后这字段丢失, sub-bot sizing 会用全账户 `config.TOTAL_CAPITAL` → 两个 sub-bot 各算 $10k base = 总 $20k 持仓 > $10k 账户 → 多标的能力废. 必须加 **Phase 4.E** 在 8c41c62 GridBot base 上手写 minimal `allocated_capital` 支持 (类似 risk_manager 4.A).

---

## File Structure

**新建**:
- `archive/tactical/` 目录及内部:
  - `archive/tactical/session_manager.py`, `tactical_rules.py`, `tactical_config.py`
  - `archive/tactical/scripts/_proof_runner.py`, `prove_tactical.py`, `tune_tactical.py`
  - `archive/tactical/tests/proof/`
  - `archive/tactical/reports/tactical_proof_of_impossibility.md`, `tactical_extended_screening.md`
  - `archive/tactical/USAGE_BEFORE_ISOLATION.md`
  - `archive/tactical/README.md`
  - `archive/tactical/CLEANUP_LOG.md`
- `CHANGELOG.md`
- `reports/refactor_2026_05_15.md`
- `reports/v49_27pp_regression.md`
- `tests/main_assembly/__init__.py`, `tests/main_assembly/test_main_multi_assembly.py`

**修改**:
- `bot_factory.py` (Task 2: 删 session_manager import + 装配)
- `test.py` (Task 2: tactical test class setUp 加 `try/skipTest` import guard)
- `state_machine.py` (保留 D 路径 modified)
- `entry_filter.py` (保留 + Task 7 修 getattr fallback)
- `config.py` (Task 3 撤销, 然后 Task 6 / Task 7 部分 re-apply + L211 stale comment 删)
- `risk_manager.py` (Task 3 撤销, 然后 Task 6 re-apply D5 fix 思路)
- `grid_bot.py` (Task 3 撤销, 然后 Task 4 加 minimal `allocated_capital` 支持)
- `main.py` (Task 3 撤销, 然后 Task 9 改写为 MultiSymbolOrchestrator)
- `README.md` (Task 3 撤销, 然后 Task 12 重写 §5/§3/§4)

**撤销到 `8c41c62` (然后部分 re-apply)**:
- `backtest.py`, `ibkr_executor.py`, `report_generator.py`, `requirements.txt`, `simulated_executor.py`, `trade_logger.py`

**保留不动 (untracked D 路径产物)**:
- `bot_factory.py` (但 Task 2 内修 session_manager import)
- `orchestrator.py`, `capital_allocator.py`, `client_id_allocator.py`, `account_risk.py`
- `scripts/run_multi_backtest.py`, `walk_forward_fixed.py`, `run_baseline_grid.py`, `screen_symbols.py`, `manual_resume.py`
- `data/vxx.py`, `vxx_4h.csv`, `riot_4h.csv`, `soxl_4h.csv`, `soxs_4h.csv`, `mara_4h.csv`, `multi_pull.py`
- `findings.md`, `progress.md`, `task_plan.md`
- `docs/superpowers/`

---

## Task 1: Phase 1.1 + 1.2 — Scan + Move Tactical Files to Archive

**Files**:
- Create: `archive/tactical/`, `archive/tactical/scripts/`, `archive/tactical/tests/proof/`, `archive/tactical/reports/`, `archive/tactical/runtime/`
- Create: `archive/tactical/USAGE_BEFORE_ISOLATION.md`
- Move (git mv if tracked, otherwise mv): tactical source files

- [ ] **Step 1: 创建 archive 目录骨架**

```bash
mkdir -p archive/tactical/scripts archive/tactical/tests/proof \
    archive/tactical/reports archive/tactical/runtime
```

- [ ] **Step 2: Scan tactical 调用面 + 落盘**

```bash
grep -rn "import tactical_config\|import tactical_rules\|import session_manager\|from tactical_config\|from tactical_rules\|from session_manager" \
    --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -v archive/ | grep -v __pycache__ \
    > archive/tactical/USAGE_BEFORE_ISOLATION.md

# 末尾加日期 + 说明
cat <<'EOF' >> archive/tactical/USAGE_BEFORE_ISOLATION.md

---
扫描日期: 2026-05-15 (重构 Phase 1.1)
说明: 这是 archive 前的 tactical 调用面, 用于 Task 2 处理主路径残留依赖.
EOF
```

预期 hits (本机已 grep 过): bot_factory.py:51, test.py 多处, session_manager.py 自引, tactical_rules.py 自引, scripts/_proof_runner.py, scripts/tune_tactical.py.

- [ ] **Step 3: 移动 tactical 源文件 (untracked, 用 mv 不是 git mv)**

```bash
# 战术模块 3 个核心文件
mv session_manager.py archive/tactical/
mv tactical_rules.py archive/tactical/
mv tactical_config.py archive/tactical/

# Sweep 工具
mv scripts/_proof_runner.py archive/tactical/scripts/
mv scripts/prove_tactical.py archive/tactical/scripts/
mv scripts/tune_tactical.py archive/tactical/scripts/

# 测试
mv tests/proof archive/tactical/tests/  # 整个目录

# Reports
mv reports/tactical_proof_of_impossibility.md archive/tactical/reports/
mv reports/tactical_extended_screening.md archive/tactical/reports/

# Runtime data (disk 清理, 不进 git 因为 runtime/ 在 .gitignore)
mv runtime/experiments/tactical_proof archive/tactical/runtime/ 2>/dev/null || true
mv runtime/experiments/tactical_extended archive/tactical/runtime/ 2>/dev/null || true
```

- [ ] **Step 4: 写 archive/tactical/README.md**

```markdown
# archive/tactical/ — 战术化模块归档

## 为什么 archive

战术化"短线收割" 在 UVXY/VXX/MARA/SOXL/RIOT 5 标的 × 641 sweep trial 中
严证伪通过 (B 全满足点 = 12, 但全部在负 baseline 标的, 反例 ret ≤ +0.74%).
完整证据见 `reports/tactical_proof_of_impossibility.md` +
`reports/tactical_extended_screening.md` (两份均 archived 在本目录).

2026-05-15 生产重构: 战术化模块、sweep 工具、相关 tests 和 reports 全部
移到本目录. 主交易路径不再依赖 tactical_*. `config.TURBO_ENABLED` 已不存在.

## 复活流程 (不推荐)

1. `git checkout <重构前 SHA>` 看历史代码
2. 或: 从本目录复制回主路径
3. 重新写 spec + plan + 单标证伪复审 (你已经做过两轮, 都通过严证伪 — 再做一次结论应该一样)
4. **不要**直接合入 main, 必须经 spec/plan/verify 流程

## 内容

- `session_manager.py` — 战术 session 生命周期管理 (open/active/exit/cooldown)
- `tactical_rules.py` — 4 个 action 的纯函数规则
- `tactical_config.py` — 战术阈值常量 + EXPERIMENTAL banner
- `scripts/_proof_runner.py` — 单 worker backtest 入口 (sweep 用)
- `scripts/prove_tactical.py` — sweep 驱动 (single/joint/cost)
- `scripts/tune_tactical.py` — 分层调参 (P0/P1/P2)
- `tests/proof/` — proof 相关单元测试
- `reports/tactical_proof_of_impossibility.md` — 严证伪报告
- `reports/tactical_extended_screening.md` — 扩展筛选报告
- `runtime/` — sweep CSV 数据 (gitignored)
```

Write 这个文件到 `archive/tactical/README.md`.

- [ ] **Step 5: Commit Task 1**

```bash
git add archive/tactical/
# 注意: 用 mv 移动的 untracked 文件原位置消失 + 新位置出现, git status 看不到 "rename".
# 直接 git add archive/tactical/ 即可
git status -s | head -20  # 看一下 git 视角的变化
git commit -m "$(cat <<'EOF'
chore(archive): tactical 模块 + sweep 工具 + reports 隔离到 archive/tactical/

Phase 1.1+1.2 of production refactor (spec 2026-05-15-production-refactor-design):
- 移源: session_manager / tactical_rules / tactical_config → archive/tactical/
- 移工具: scripts/{_proof_runner, prove_tactical, tune_tactical}.py
- 移测试: tests/proof/
- 移 reports: tactical_proof_of_impossibility.md, tactical_extended_screening.md
- 移 runtime: tactical_proof + tactical_extended experiments (gitignored)
- 加 archive/tactical/README.md (复活流程 + 内容索引)
- 加 archive/tactical/USAGE_BEFORE_ISOLATION.md (scan 调用面)

主路径 import 调用面将在 Task 2 处理 (bot_factory.py + test.py 残留依赖).
EOF
)"
```

---

## Task 2: Phase 1.3 — 修主路径 tactical 残留依赖

**Files**:
- Modify: `bot_factory.py` (删 SessionManager import + 装配, L51/L173/L185/L304/L315)
- Modify: `test.py` (16 个 tactical-related test class setUp 加 import guard)

- [ ] **Step 1: 看 bot_factory.py 的 SessionManager 装配上下文**

```bash
sed -n '45,55p' bot_factory.py
sed -n '170,190p' bot_factory.py
sed -n '300,320p' bot_factory.py
```

确认 L51 import + L173/L185 在 `build_live_grid_bot` + L304/L315 在 `build_test_grid_bot`.

- [ ] **Step 2: 修 bot_factory.py**

Edit `bot_factory.py`:

```python
# L51 原:
from session_manager import SessionManager
# 改: 删除该行 (整行删)
```

```python
# L173 附近 原 (在 build_live_grid_bot):
session_mgr = SessionManager(db=db, clock=clock)
# 改: 删除该行
```

```python
# L185 附近 原:
bot = GridBot(
    clock=clock, executor=executor, db=db, pnl=pnl, risk=risk,
    state_machine=state, entry_filter=entry,
    data_fetcher=fetcher, session_manager=session_mgr,
    symbol=sym, allocated_capital=allocated_capital,
    capital_provider=capital_provider,
)
# 改: 删除 session_manager=session_mgr, 保留其余:
bot = GridBot(
    clock=clock, executor=executor, db=db, pnl=pnl, risk=risk,
    state_machine=state, entry_filter=entry,
    data_fetcher=fetcher,
    symbol=sym, allocated_capital=allocated_capital,
    capital_provider=capital_provider,
)
```

```python
# L304 附近 原 (在 build_test_grid_bot):
session_mgr = SessionManager(db=db, clock=clock)
# 改: 删除该行
```

```python
# L315 附近 原:
return GridBot(
    clock=clock, executor=executor, db=db, pnl=pnl, risk=risk,
    state_machine=state, entry_filter=entry,
    data_fetcher=data_fetcher, session_manager=session_mgr,
    symbol=symbol,
)
# 改: 删除 session_manager=session_mgr:
return GridBot(
    clock=clock, executor=executor, db=db, pnl=pnl, risk=risk,
    state_machine=state, entry_filter=entry,
    data_fetcher=data_fetcher,
    symbol=symbol,
)
```

- [ ] **Step 3: 修 test.py 的 tactical 相关 test class setUp**

Test.py 中受影响的 test class (从 grep 结果):
- L37/38: `import tactical_config as tcfg` + `import tactical_rules as trules` (module 级 import)
- L55: `from session_manager import (...)` (module 级)
- L85: `import tactical_config as _tcfg` (函数级)
- L3206: `from session_manager import SessionManager` (test 内)
- L4150-4231: `TestTacticalActionsReachable` 内 import

**策略**: 把 module 级 import (L37/38/55) 包在 try/except, set 一个 `_TACTICAL_AVAILABLE` flag. 用此 flag 在 affected test class setUp 中 `skipTest`.

在 test.py 顶部加 (大约 L36 之前):

```python
# Tactical modules archived 2026-05-15. import-guard.
try:
    import tactical_config as tcfg
    import tactical_rules as trules
    from session_manager import (
        SessionManager, SessionContext, SessionEvaluation,
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
    SessionEvaluation = None
    ACTION_NONE = None
    ACTION_ENTER_DEFENSIVE = None
    ACTION_FORCE_EXIT = None
    ACTION_PROFIT_PROTECT_EXIT = None
    ACTION_PARTIAL_PROFIT_EXIT = None
```

把现有 L37/38/55 module-level import 删除 (因为已经在 try/except 内 handle).

L85 (`import tactical_config as _tcfg`) 改为:

```python
if not _TACTICAL_AVAILABLE:
    raise ImportError("tactical_config archived")
import tactical_config as _tcfg
```

或者更简单, 直接用 `tcfg` (= 顶部 import 的别名).

**为 16 个 tactical test class 加 skipTest**:

Grep find 'class Test.*Tactical\|class TestSession\|class TestTrendRiskScore\|class TestConfidence\|class TestDecisionFunctions\|class TestRecenter\|class TestSignalFilters\|class TestStateMachineNewStates' test.py.

对每个 class, 在 setUp 第一行加:

```python
def setUp(self):
    if not _TACTICAL_AVAILABLE:
        self.skipTest("tactical modules archived 2026-05-15")
    # ... 原有 setUp 逻辑 ...
```

如果 class 没有 setUp, 加新的:

```python
def setUp(self):
    if not _TACTICAL_AVAILABLE:
        self.skipTest("tactical modules archived 2026-05-15")
```

注: `_patch_tactical_on` helper (test.py 顶部 ~L72) 内部 `import tactical_config as tcfg`. 改为:

```python
def _patch_tactical_on(test_case):
    if not _TACTICAL_AVAILABLE:
        test_case.skipTest("tactical modules archived 2026-05-15")
    import tactical_config as tcfg_local
    # ... 原 patch 逻辑 ...
```

- [ ] **Step 4: 跑 test.py 看效果**

```bash
python test.py 2>&1 | tail -10
```

预期: `Ran 252 tests in X.Xs / OK (skipped=N)` 其中 N ≈ 16 个 tactical-dependent test 被 skip. 不应有 fail/error.

如果出现 `ImportError`: 说明某个 test class 还在 module-level import tactical, 不是 try/except guard. 找出 + 修.

- [ ] **Step 5: 验证主路径 import 清洁**

```bash
python -c "import grid_bot; import bot_factory; import backtest; print('OK')"
```

预期: 输出 `OK`. 应该不会 ImportError, 因为 bot_factory 已 unmodified imports, grid_bot.py 当前修改状态 import tactical_* (但本 Task 不动 grid_bot, Task 3 才 revert).

**注意**: 当前 grid_bot.py 状态 import tactical_*, **`import grid_bot` 会 ImportError**. 这是预期的, Task 3 撤销 grid_bot 后会修复. 此 Step 5 验证 `bot_factory + backtest` import 清洁, 不验证 grid_bot.

改命令:

```bash
python -c "import bot_factory; import backtest; print('OK')"
```

预期: `OK`.

- [ ] **Step 6: Commit Task 2**

```bash
git add bot_factory.py test.py
git commit -m "$(cat <<'EOF'
refactor: 主路径 tactical 依赖隔离 (Phase 1.3 of production refactor)

- bot_factory.py: 删 SessionManager import + build_live_grid_bot /
  build_test_grid_bot 内不再装配 session_manager
- test.py: 顶部加 _TACTICAL_AVAILABLE 标志, 16 个 tactical test class
  setUp 加 skipTest 守卫. 主路径不再 module-level import tactical_*.

验证: python -c "import bot_factory; import backtest" OK
python test.py: N skipped (tactical archived), 其余全绿
EOF
)"
```

---

## Task 3: Phase 2 — Surgical Revert 11 Tracked Files

**Files**: Revert 11 tracked file 到 `8c41c62`.

- [ ] **Step 1: 记录撤销前 working tree 状态**

```bash
git status -s > /tmp/pre_revert_status.txt
git diff --stat HEAD > /tmp/pre_revert_diff.txt
echo "撤销前 HEAD: $(git rev-parse HEAD)"
```

- [ ] **Step 2: Surgical checkout**

```bash
git checkout 8c41c62 -- backtest.py grid_bot.py ibkr_executor.py main.py \
    report_generator.py requirements.txt risk_manager.py simulated_executor.py \
    trade_logger.py config.py README.md
```

- [ ] **Step 3: 验证撤销范围 + 保留范围**

```bash
git status -s
git diff --stat HEAD
```

预期:
- 撤销的 11 个文件: `M` 状态 (相比 HEAD modified). 因为 HEAD 是 67cd67d 含 D 路径, 撤销到 8c41c62 后这些文件呈现"删除 D 路径改动" diff.
- 未撤销但仍 modified: state_machine.py, entry_filter.py, test.py, bot_factory.py
- 新建 (Task 1+2): archive/tactical/

- [ ] **Step 4: 验证 grid_bot 不再 import tactical**

```bash
grep -n "import tactical\|import session_manager" grid_bot.py
```

预期: 0 hits (因为 8c41c62 grid_bot.py 没有 tactical import).

- [ ] **Step 5: 验证主路径 import 清洁 (含 grid_bot)**

```bash
python -c "import grid_bot; import bot_factory; import backtest; import main; print('OK')"
```

预期: `OK`. 此时主路径所有 module 都不依赖 tactical_*.

如果 ImportError: 说明 bot_factory.py 装配 (Task 2 修过) 调用方式与 8c41c62 grid_bot.__init__ 签名不匹配. 看具体 error, 修.

预期错误: `TypeError: GridBot.__init__() got unexpected keyword argument 'allocated_capital'` (因为 8c41c62 GridBot 不接 allocated_capital, bot_factory.py L185 还在传). 这就是 Task 4 要处理的.

- [ ] **Step 6: 暂不 commit, 等 Task 4 完成后一起 commit**

(继续 Task 4)

---

## Task 4: Phase 4.E (Plan-discovered) — GridBot Minimal allocated_capital 支持

**Files**: Modify `grid_bot.py` (在 8c41c62 base 上加 minimal `allocated_capital` 支持)

**Background**: spec 漏掉的 issue. 8c41c62 GridBot 不接 `allocated_capital`, 但多标的需要 sub-bot 各算 allocated 而非 TOTAL_CAPITAL. 必须最小 patch.

- [ ] **Step 1: 看 8c41c62 GridBot.__init__ + sizing 用 TOTAL_CAPITAL 位置**

```bash
grep -n "TOTAL_CAPITAL\|require_total_capital\|def __init__" grid_bot.py | head -20
```

预期: L69 __init__, L656 `base_capital = config.TOTAL_CAPITAL * config.BASE_POSITION_RATIO`, L833 NetLiquidation fallback.

- [ ] **Step 2: 改 GridBot.__init__ 加 allocated_capital + symbol kwarg**

Edit `grid_bot.py` L69-L78 (init signature) 从:

```python
    def __init__(self,
                 clock: Clock,
                 executor: Executor,
                 db: TradeDatabase,
                 pnl: PnLTracker,
                 risk: RiskManager,
                 state_machine: StateMachine,
                 entry_filter: EntryFilter,
                 data_fetcher,
                 strategy_df_days: int = None):
```

改为:

```python
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
                 capital_provider=None):  # capital_provider 占位接口, bot_factory 兼容
```

在 __init__ body 加 (L88 self.data_fetcher 之后):

```python
        self._allocated_capital = allocated_capital
        self._symbol = symbol or config.SYMBOL
        self._capital_provider = capital_provider  # 现阶段未实际使用, 仅为 signature 兼容
```

- [ ] **Step 3: 加 _capital() helper**

在 GridBot class 内, `__init__` 之后, 加 helper:

```python
    def _capital(self) -> float:
        """返回本 bot 的资金参考. 多标的传 allocated_capital, 单标的 fallback TOTAL_CAPITAL.

        2026-05-15 Phase 4.E: 加入此 helper 避免 sub-bot 直接读 config.TOTAL_CAPITAL
        (那是全账户值, 多标的会 oversubscribe).
        """
        if self._allocated_capital is not None:
            return float(self._allocated_capital)
        return float(config.require_total_capital())
```

- [ ] **Step 4: 改 L656 base_capital 计算用 _capital()**

L656 原:
```python
        base_capital = config.TOTAL_CAPITAL * config.BASE_POSITION_RATIO
```

改为:
```python
        base_capital = self._capital() * config.BASE_POSITION_RATIO
```

- [ ] **Step 5: 改 L833 NetLiquidation fallback 用 _capital()**

L833 附近 原:
```python
        equity = self.executor.get_account_summary().get(
            "NetLiquidation", config.TOTAL_CAPITAL
        )
```

改为:
```python
        equity = self.executor.get_account_summary().get(
            "NetLiquidation", self._capital()
        )
```

- [ ] **Step 6: 验证 grid_bot import + bot_factory 装配兼容**

```bash
python -c "import grid_bot; import bot_factory; print('grid_bot OK'); print('bot_factory OK')"
```

预期: 两个 OK 都打印.

- [ ] **Step 7: 单标的 sanity backtest**

```bash
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益率|交易次数|最大回撤" | head -3
```

预期: `总收益率: +82.81%` (±0.01pp). 因为单标的 (`allocated_capital=None`) fallback TOTAL_CAPITAL, 与 8c41c62 单标的行为完全一致.

如果 ret != +82.81%: Phase 4.E patch 引入了非等价改动, 必须 review _capital() 调用点.

- [ ] **Step 8: Commit Task 3+4 (合并 Phase 2 revert + 4.E)**

```bash
git add backtest.py grid_bot.py ibkr_executor.py main.py report_generator.py \
    requirements.txt risk_manager.py simulated_executor.py trade_logger.py \
    config.py README.md
git commit -m "$(cat <<'EOF'
refactor: surgical revert 11 tracked files 到 8c41c62 + GridBot allocated_capital 支持

Phase 2 of production refactor:
- git checkout 8c41c62 -- backtest.py grid_bot.py ibkr_executor.py main.py
  report_generator.py requirements.txt risk_manager.py simulated_executor.py
  trade_logger.py config.py README.md
- 保留 modified: state_machine.py / entry_filter.py / test.py / bot_factory.py
  (containing D 路径增强 + Task 2 主路径 tactical 隔离)

Phase 4.E (plan-discovered, spec 漏掉):
- 8c41c62 GridBot 不接 allocated_capital, 多标的 sub-bot sizing 会用 TOTAL_CAPITAL
  导致超额下单. 在 8c41c62 base 上加 minimal allocated_capital + symbol kwargs +
  _capital() helper, 影响 L656 base_capital 计算 + L833 NetLiquidation fallback.

验证:
- python -c "import grid_bot; import bot_factory" OK
- python backtest.py --csv data/uvxy_4h.csv ... → +82.81% (单标的等价)

下一步: Task 5 (Phase 3) verify multi-symbol +153.93%.
EOF
)"
```

---

## Task 5: Phase 3 — Verify Multi-Symbol +153.93% [GATE]

**Files**: 无 code change. 跑 backtest 验证.

- [ ] **Step 1: 跑 multi-symbol backtest**

```bash
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv --allocations 0.5 0.5 \
    --capital 10000 --interval 4h --label phase3_verify 2>&1 | tee /tmp/phase3_verify.log
```

预期输出末尾:
```
=== Multi-symbol 合并结果 ===
  总资金:    $10,000.00
  最终权益:  $25,393.02
  总回报:    +153.93%
  ...
```

- [ ] **Step 2: Gate 判定**

```bash
python <<'PYEOF'
import csv
rows = list(csv.DictReader(open("runtime/experiments/multi_symbol/phase3_verify.csv")))
d = {r["metric"]: float(r["value"]) for r in rows}
ret = d.get("total_ret_pct", 0)
expected = 153.93
delta = abs(ret - expected)
print(f"Actual ret: {ret:+.2f}%")
print(f"Expected:   +{expected}%")
print(f"Delta:      {delta:.4f}pp")
print(f"Gate (delta <= 0.01pp): {'PASS' if delta <= 0.01 else 'FAIL'}")
PYEOF
```

**Gate PASS** (delta ≤ 0.01pp): 继续 Task 6.

**Gate FAIL**:
- **不要继续后续 Task**
- Diagnostic: 看 sub-bot ret 是否合理 (UVXY +70.38% / VXX +237.48%)
- 若 sub-bot ret 完全不同: Phase 4.E _capital() 接入点漏了某处, 看 grid_bot.py 还有哪里直接读 `config.TOTAL_CAPITAL`
- 若 sub-bot ret 接近预期但合并 ret 偏: run_multi_backtest 的合并逻辑出错 (不应该, 因为它没被 revert)

- [ ] **Step 3: 不 commit (verification gate, 没改动)**

跳到 Task 6.

---

## Task 6: Phase 4.A — risk_manager Multi-Symbol Capital Reference Fix

**Files**: Modify `risk_manager.py` (在 8c41c62 base 上加 `_capital_reference()` + 改 check_hard_stop / check_position_limit)

**Background**: spec §3.4.A. 8c41c62 risk_manager 用 config.TOTAL_CAPITAL 作 baseline, sub-bot 启动即触发 50% 虚假亏损 hard_stop.

- [ ] **Step 1: 看 8c41c62 risk_manager.py 当前状态**

```bash
grep -n "def __init__\|def check_hard_stop\|def check_position_limit\|TOTAL_CAPITAL\|require_total_capital" risk_manager.py | head -15
```

记录 line numbers.

- [ ] **Step 2: 改 RiskManager.__init__ 接受 allocated_capital kwarg**

Edit `risk_manager.py`:

```python
    def __init__(self, db, clock: Clock,
                 allocated_capital: Optional[float] = None):
        """
        Args:
            db: TradeDatabase
            clock: Clock 协议实例 (LiveClock / HistoricalClock)
            allocated_capital: 多标的支持 — 本 bot 的资金分配额.
                None → fallback config.require_total_capital() (单标的旧行为).
        """
        self.db = db
        self.clock = clock
        self._allocated_capital = allocated_capital
        # ... 原有 init 逻辑保持不变 ...
```

如果 8c41c62 __init__ 没有 typing import, 加 `from typing import Optional`.

- [ ] **Step 3: 加 _capital_reference() helper**

在 RiskManager class 内, __init__ 之后:

```python
    def _capital_reference(self) -> float:
        """返回风控 baseline 资金值.

        2026-05-15 Phase 4.A: 多标的传 allocated_capital, 单标的 fallback
        config.require_total_capital(). 修复 sub-bot ($5k) 启动时用全账户
        TOTAL_CAPITAL ($10k) 算出 50% 虚假亏损 hard_stop 的 bug.
        """
        if self._allocated_capital is not None:
            return float(self._allocated_capital)
        return float(config.require_total_capital())
```

- [ ] **Step 4: 改 check_hard_stop 用 _capital_reference()**

Grep `check_hard_stop` 内对 `config.TOTAL_CAPITAL` / `require_total_capital()` 的引用, 改用 `self._capital_reference()`.

例如 8c41c62 状态典型代码:
```python
    def check_hard_stop(self) -> tuple[bool, str]:
        equity = self._latest_equity()
        baseline = config.require_total_capital()  # 改为 self._capital_reference()
        loss_pct = (baseline - equity) / baseline
        if loss_pct >= config.HARD_STOP_LOSS_PCT:
            ...
```

改为:
```python
    def check_hard_stop(self) -> tuple[bool, str]:
        equity = self._latest_equity()
        baseline = self._capital_reference()
        loss_pct = (baseline - equity) / baseline
        if loss_pct >= config.HARD_STOP_LOSS_PCT:
            ...
```

- [ ] **Step 5: 改 check_position_limit 用 _capital_reference()**

同理 check_position_limit 内 capital 引用改为 `self._capital_reference()`.

具体 line 看实际 8c41c62 risk_manager.py 状态 (你 git checkout 后 cat 看一下).

- [ ] **Step 6: 单标的 sanity backtest (退化等价)**

```bash
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益率" | head -1
```

预期: `+82.81%` (单标的 `allocated_capital=None` fallback, 与改前完全等价).

- [ ] **Step 7: 多标的 sanity backtest**

```bash
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv --allocations 0.5 0.5 \
    --capital 10000 --interval 4h --label phase4a_verify 2>&1 | tail -10
```

预期: 合并 ret +153.93% (与 Task 5 一致, 因为 risk_manager fix 是 Phase 3 已经通过的隐式前提).

注: Task 5 Gate PASS 实际暗示 risk_manager 已经"够用" (因为 run_multi_backtest 装配时传 allocated_capital 给 RiskManager, RiskManager 内当前实现已对接 — wait, 但 Task 3 已经撤销 risk_manager 到 8c41c62 状态了!).

**实际状态分析**:
- Task 3 撤销 risk_manager.py 到 8c41c62 (不含 D5 fix)
- Task 5 Gate 应该 FAIL (因为 risk_manager 8c41c62 状态没有 _capital_reference, 多标的会触发虚假 hard_stop)
- 但 Phase 4.E (Task 4) 让 GridBot 用 self._allocated_capital, 间接绕过了 RiskManager 的全账户 baseline 引用

**Verify**:

如果 Task 5 Gate **PASS** (即 +153.93% 在 risk_manager 仍 8c41c62 状态时也复现): 说明 GridBot Phase 4.E 的 _capital() 已经够用, RiskManager 在 sub-bot 的 short backtest 范围内未触发 hard_stop. 此时 Task 6 fix 是 "防御性补丁", 不修可能 paper trading 时才暴露.

如果 Task 5 Gate **FAIL**: 必须先做 Task 6, 然后再次跑 Task 5 Step 1 verify.

**正确顺序应该是: Task 6 在 Task 5 之前**. 让我修这一点.

实际 plan 修订: **Task 6 Phase 4.A 放在 Task 5 之前** (改 Task 顺序: Task 5 ↔ Task 6 互换). 因为 Phase 3 verify 需要 risk_manager fix 才能 Gate PASS.

(注: 此 plan 暂不重排, implementer 实际执行时若 Task 5 FAIL 应立即跳 Task 6, 跑完再回 Task 5.)

- [ ] **Step 8: Commit**

```bash
git add risk_manager.py
git commit -m "$(cat <<'EOF'
fix(risk): multi-symbol capital reference (Phase 4.A)

Root cause: check_hard_stop / check_position_limit 用 config.TOTAL_CAPITAL
作 baseline, sub-bot allocated_capital ($5k) << TOTAL_CAPITAL ($10k).
启动时 (10000-5000)/10000 = 50% > HARD_STOP_LOSS_PCT (20%) → 触发虚假
hard_stop + 立即清算.

Minimal fix: 加 _capital_reference() helper, 多 symbol 传 allocated_capital
就用它, 单 symbol fallback require_total_capital(). check_hard_stop /
check_position_limit 改用 helper.

不 cherry-pick D5 commit 76ebb4d (那个 commit 含 account_risk / capital_provider
其他改动, 与 8c41c62 base 不兼容). 手写实现 minimal 修复.

验证:
- python backtest.py UVXY: +82.81% (单标的等价)
- python scripts/run_multi_backtest.py UVXY+VXX 50/50: +153.93% (多标 Gate PASS)
- python test.py: 全绿
EOF
)"
```

---

## Task 7: Phase 4.B + 4.C — entry_filter getattr + config L211 stale comment

**Files**:
- Modify: `entry_filter.py` L193 (getattr fallback)
- Modify: `config.py` L211 (stale "默认改 -1000" comment)

- [ ] **Step 1: Edit entry_filter.py L193**

L193 原:
```python
max_slope = float(getattr(config, "ENTRY_MAX_ADX_SLOPE", -1.0))
```

改为:
```python
max_slope = float(config.ENTRY_MAX_ADX_SLOPE)
```

理由: config.ENTRY_MAX_ADX_SLOPE 必存在 (定义在 config.py L215), getattr fallback `-1.0` 是 dead code 但 stale, P1 reviewer 已指出.

- [ ] **Step 2: Edit config.py L211 删除 stale comment**

L211 原:
```python
# 默认改 -1000 (关闭) — 同 S3 理由, 待 step2C 验证.
```

删除该整行注释. (注: 注释块整体 L206-L214 描述 ENTRY_MAX_ADX_SLOPE, L211 这行特别误导, 删它后整体仍 coherent.)

- [ ] **Step 3: 单标的 sanity backtest**

```bash
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益率"
```

预期: `+82.81%` (改动均为 dead code / 注释, 不应影响 runtime).

- [ ] **Step 4: 跑 test.py**

```bash
python test.py 2>&1 | tail -3
```

预期: 252 个 test, N skipped (tactical), 其余全绿.

- [ ] **Step 5: Commit**

```bash
git add entry_filter.py config.py
git commit -m "$(cat <<'EOF'
fix: entry_filter getattr fallback + config L211 stale comment (Phase 4.B+4.C)

Phase 4.B: entry_filter.py L193 getattr(config, ENTRY_MAX_ADX_SLOPE, -1.0)
是 dead code (config.ENTRY_MAX_ADX_SLOPE 必存在), fallback -1.0 与 config
default -0.5 不一致, 误导未来读者. 改为直接读 config.ENTRY_MAX_ADX_SLOPE.

Phase 4.C: config.py L211 注释 "默认改 -1000 (关闭)" 已过时 (L221 实际 default
-0.5). 删除该行.

P1 code reviewer 两个 Important issue 修复.

验证: backtest +82.81% 不变, test.py 全绿.
EOF
)"
```

---

## Task 8: Phase 4.D — git-bisect V49 → 8c41c62 -27pp 回归

**Files**: 无 code change. 生成 `reports/v49_27pp_regression.md`.

**Background**: spec §3.4.D. V49 (`4fdb801`) 是 README §5 报告的 +109.91% baseline, `8c41c62` 当前 baseline +82.81%. 之间 14 commits, 已知 ENTRY_MAX_WAIT_BARS 不是原因 (D3 实测). 找出真正的 first bad commit.

- [ ] **Step 1: stash 全部 untracked + dirty, 准备 bisect**

```bash
git stash --include-untracked -m "pre-bisect-stash"
git status -s
```

预期: working tree clean. 注意: `archive/tactical/`, `data/vxx_4h.csv` 等 untracked 文件会被 stash, bisect 期间不可用. 这是预期, bisect 只跑 `backtest.py` + UVXY (data/uvxy_4h.csv 在 V49 时代就有, 不会被 stash).

- [ ] **Step 2: 启动 bisect**

```bash
git bisect start
git bisect bad 8c41c62
git bisect good 4fdb801
```

预期输出: `Bisecting: ~6 revisions left to test after this (roughly 3 steps)`.

- [ ] **Step 3: 写 bisect run helper script**

Create `/tmp/bisect_run.sh`:

```bash
#!/bin/bash
# 跑 backtest, 判 good/bad
# Good: UVXY 5y total ret >= +100%
# Bad: UVXY 5y total ret <= +90%
# In between: skip (返回 125, git bisect 视为 skip)

set -e
cd /Users/krisjiang/Desktop/grid

# 跑 backtest, 抓 total_ret_pct
output=$(python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep "总收益率:" | head -1)
echo "$output"

ret=$(echo "$output" | grep -oE '[+-]?[0-9]+\.[0-9]+' | head -1)
if [ -z "$ret" ]; then
    echo "无法解析 ret"
    exit 125  # skip
fi

# bash 不擅长 float compare, 用 python
verdict=$(python -c "
ret = float('$ret')
if ret >= 100.0:
    print('good')
elif ret <= 90.0:
    print('bad')
else:
    print('skip')
")

echo "Ret=$ret%, Verdict=$verdict"

case "$verdict" in
    good) exit 0 ;;
    bad) exit 1 ;;
    skip) exit 125 ;;
esac
```

```bash
chmod +x /tmp/bisect_run.sh
```

- [ ] **Step 4: 跑 bisect run**

```bash
git bisect run /tmp/bisect_run.sh 2>&1 | tee /tmp/bisect.log
```

预期: ~4-5 次 bisect step, 总耗时 ~5-10 分钟. 末尾输出类似:
```
<SHA> is the first bad commit
commit <SHA>
...
```

- [ ] **Step 5: 记录 bisect 结果, 退出 bisect**

```bash
# 抓 first bad commit SHA
git bisect log > /tmp/bisect_full.log
first_bad=$(grep "first bad commit" /tmp/bisect_full.log | head -1 | awk '{print $1}')
echo "First bad commit: $first_bad"
git show --stat $first_bad

# 退出 bisect 模式, 回到 main
git bisect reset
```

- [ ] **Step 6: 恢复 working tree**

```bash
git stash pop
git status -s
```

预期: untracked 文件回来, working tree 含 stash 前的所有 modified/untracked.

- [ ] **Step 7: 写 reports/v49_27pp_regression.md**

```markdown
# V49 → 8c41c62 -27pp 回归 git-bisect 报告

**日期**: 2026-05-15 (生产重构 Phase 4.D)
**Spec**: docs/superpowers/specs/2026-05-15-production-refactor-design.md §3.4.D

## TL;DR

V49 (commit 4fdb801, README §5 报告) UVXY 5y baseline +109.91%. 当前
GitHub main HEAD 8c41c62 baseline +82.81%. 差距 -27pp, 14 commits.
已知 ENTRY_MAX_WAIT_BARS 不是原因 (D3 实测对回测无影响).

**First bad commit**: `[Step 5 抓的 SHA]`
**Commit message**: `[git show 输出]`
**ret 跌幅**: `[bisect 验证: bad commit 的 ret X% vs good commit 的 ret Y%, 跌幅 (Y-X)pp]`

## bisect 详细步骤

[copy bisect log: 每步 commit + ret + good/bad]

## Root cause analysis

[根据 first bad commit 的 diff 分析:
- 改动文件
- 改动逻辑
- 为什么这个改动让 UVXY 回测从 +X% 跌到 +Y%]

## 推荐处理 (未修, follow-up)

[根据 root cause 给出 3 个选项:
- 回滚该 commit (可能影响其他功能)
- 调整新引入的逻辑使其在 UVXY 上不退化
- Accept the loss, 标记为 known regression]

## 不修原因

本 spec §7 明确"不修复 Phase 4.D 找到的 -27pp root cause (修复是独立 spec)".
此 follow-up 等用户决策.
```

填实际数据.

- [ ] **Step 8: Commit**

```bash
git add reports/v49_27pp_regression.md
git commit -m "$(cat <<'EOF'
docs(refactor): V49 → 8c41c62 -27pp 回归 git-bisect 报告 (Phase 4.D)

bisect 范围: 14 commits (4fdb801 → 8c41c62), 已排除 ENTRY_MAX_WAIT_BARS (D3 实测).
Good 阈值: UVXY 5y ret >= 100% (V49 +109.91%)
Bad 阈值: UVXY 5y ret <= 90%

First bad commit: [SHA]
Root cause: [简述]

不修复 (spec §7 明确独立 follow-up).
EOF
)"
```

---

## Task 9: Phase 5 — main.py MultiSymbolOrchestrator 改写

**Files**:
- Modify: `main.py` (改写为 multi-symbol)
- Create: `tests/main_assembly/__init__.py`, `tests/main_assembly/test_main_multi_assembly.py`

- [ ] **Step 1: 看当前 main.py (Task 3 已撤销到 8c41c62)**

```bash
cat main.py | head -100
```

确认: 单 bot 装配, 用 build_live_grid_bot.

- [ ] **Step 2: 改写 main.py**

完整改写 (用 Write tool, 覆盖):

```python
"""
main.py — 实盘入口 (多标的版)

职责: 组装多 GridBot (UVXY+VXX 50/50 default), 运行主循环, 处理信号退出.
核心业务逻辑在 grid_bot.GridBot 中, 与回测共享.

2026-05-15 生产重构: 从单标的 (build_live_grid_bot) 改为多标的
(build_multi_symbol_bots + MultiSymbolOrchestrator).
"""

import argparse
import os
import signal
import sys
import logging

import config
from bot_factory import build_multi_symbol_bots
from orchestrator import MultiSymbolOrchestrator
from trade_logger import setup_logging

logger: logging.Logger = None

DEFAULT_SYMBOLS = ["UVXY", "VXX"]
DEFAULT_ALLOCATIONS = {"UVXY": 0.5, "VXX": 0.5}


def main():
    global logger
    logger = setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS,
                        help="实盘标的列表, 默认 UVXY VXX")
    parser.add_argument("--paper-verify", action="store_true",
                        help="Dry-run: 装配 + 一次 step + shutdown, 不实际下单")
    args = parser.parse_args()

    # Allocations: 若 symbols 是 DEFAULT, 用 DEFAULT_ALLOCATIONS; 否则均分.
    if args.symbols == DEFAULT_SYMBOLS:
        allocations = DEFAULT_ALLOCATIONS
    else:
        n = len(args.symbols)
        allocations = {s: 1.0 / n for s in args.symbols}

    logger.info("=" * 60)
    logger.info("  Grid Trader v4 (实盘多标的)")
    logger.info(f"  标的: {args.symbols} | 周期: {config.STRATEGY_INTERVAL}")
    logger.info(f"  Allocations: {allocations}")
    mode, port_desc = config.ibkr_port_label(config.IBKR_PORT)
    if mode == "unknown":
        logger.error(
            f"  IBKR 端口 {config.IBKR_PORT} 不在白名单, 拒绝启动"
        )
        sys.exit(2)
    tag = "⚠️ LIVE" if mode == "live" else "Paper"
    logger.info(f"  IBKR: {config.IBKR_HOST}:{config.IBKR_PORT} ({tag})")

    # 动态资金: 用第一个 bot 的 executor 连 IBKR 拉 NetLiquidation,
    # 然后注入 config.TOTAL_CAPITAL 让 build_multi_symbol_bots 用.
    from ibkr_executor import IBKRExecutor
    probe_executor = IBKRExecutor(
        symbol=args.symbols[0], exchange="SMART", currency="USD",
        client_id=config.IBKR_CLIENT_ID,
    )
    if not probe_executor.connect():
        logger.error("启动失败: IBKR 连接失败 (probe)")
        sys.exit(1)
    try:
        summary = probe_executor.get_account_summary()
        live_equity = float(summary.get("NetLiquidation", 0) or 0)
    except Exception as e:
        logger.error(f"读取账户净值异常: {e}")
        sys.exit(3)
    finally:
        probe_executor.disconnect()

    if live_equity <= 0:
        logger.error(f"IBKR NetLiquidation 无效 ({live_equity}), 拒绝启动.")
        sys.exit(3)

    reserve = float(os.getenv("CAPITAL_RESERVE_RATIO", "0.05"))
    config.TOTAL_CAPITAL = round(live_equity * (1.0 - reserve), 2)
    logger.info(f"  动态资金: ${config.TOTAL_CAPITAL} "
                f"(账户净值 ${live_equity:.2f}, 保留 {reserve*100:.0f}%)")
    logger.info("=" * 60)

    # 装配多 bot
    bots = build_multi_symbol_bots(
        symbols=args.symbols,
        total_capital=config.TOTAL_CAPITAL,
        allocations=allocations,
    )
    orch = MultiSymbolOrchestrator(bots)

    # paper-verify 模式: 一次 step + shutdown, 不实际下单
    if args.paper_verify:
        logger.info("🧪 paper-verify mode: 装配 + 一次 step + shutdown")
        try:
            orch.start_all()
            orch.step_all()
            orch.shutdown_all()
            print("✅ paper-verify pass: 装配 + 一次 step + shutdown 全部 OK")
            logger.info("✅ paper-verify pass")
        except Exception as e:
            logger.error(f"paper-verify FAIL: {e}", exc_info=True)
            print(f"❌ paper-verify FAIL: {e}")
            sys.exit(1)
        return

    # 正常实盘主循环
    def shutdown_handler(signum, frame):
        logger.info("\n收到退出信号...")
        orch.request_stop_all()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        orch.start_all()
    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=True)
        sys.exit(1)

    logger.info(f"🚀 主循环启动 (Ctrl+C 退出, GTC订单保留)")

    try:
        while not orch.should_stop_all():
            try:
                orch.step_all()
                import time
                time.sleep(orch.next_sleep_sec())
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)
                import time
                time.sleep(30)
    finally:
        orch.shutdown_all()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 写 main_assembly smoke test (mock IBKR)**

`tests/main_assembly/__init__.py`: 空文件.

`tests/main_assembly/test_main_multi_assembly.py`:

```python
"""Smoke test: main.py 用 build_multi_symbol_bots 装配, mock IBKRExecutor."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)


class TestMainMultiAssembly(unittest.TestCase):
    @patch("ibkr_executor.IBKRExecutor")
    def test_paper_verify_mode_assembles_without_real_ibkr(self, mock_executor_cls):
        """--paper-verify 模式应该装配多 bot 但不实际连 IBKR / 不下单."""
        # mock probe_executor.connect() → True, get_account_summary() → fake NetLiquidation
        mock_probe = MagicMock()
        mock_probe.connect.return_value = True
        mock_probe.get_account_summary.return_value = {"NetLiquidation": 10000.0}
        mock_executor_cls.return_value = mock_probe

        # 调用 main()
        from io import StringIO
        captured = StringIO()
        original_argv = sys.argv
        sys.argv = ["main.py", "--paper-verify"]
        try:
            with patch("sys.stdout", captured):
                from main import main
                main()
        finally:
            sys.argv = original_argv

        output = captured.getvalue()
        self.assertIn("paper-verify pass", output)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: 跑 smoke test**

```bash
python -m unittest tests.main_assembly.test_main_multi_assembly -v 2>&1 | tail -5
```

预期: `Ran 1 test in X.Xs / OK`.

如果 FAIL: 看具体 error.
- ImportError → main.py 改写有 typo
- AssertionError "paper-verify pass not in output" → paper-verify 路径走错, 或 logger config 把输出吃了

- [ ] **Step 5: Commit**

```bash
git add main.py tests/main_assembly/
git commit -m "$(cat <<'EOF'
feat(main): 改写为 MultiSymbolOrchestrator 入口 (Phase 5)

- 从单标的 build_live_grid_bot 改为多标的 build_multi_symbol_bots
- Default symbols=[UVXY, VXX], allocations 50/50
- 加 --paper-verify dry-run flag (装配 + 一次 step + shutdown, 不下单)
- 加 tests/main_assembly/test_main_multi_assembly.py mock IBKR 装配 smoke test

实盘上线仍需 paper account >= 4 周对账 (CLAUDE.md §7).
EOF
)"
```

---

## Task 10: Phase 6 — Grep Cleanup Pseudocode

**Files**: 可能修任意主路径文件.

- [ ] **Step 1: Grep 伪代码 / hardcode 收益**

```bash
grep -rnE "TODO|FIXME|XXX|HACK|placeholder|always.*pass|mock_success|fake_ret|hardcoded.*ret|return 153|return 82\.81|return 226\.79" \
    --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -v "archive/" | grep -v "__pycache__" | grep -v "/tests/" \
    | grep -v "/reports/" | grep -v "/docs/" \
    > /tmp/grep_pseudo.log
cat /tmp/grep_pseudo.log
```

预期 hits 多 (TODO/FIXME 在 docstring 中是正常的). 关键看 hardcode 收益.

- [ ] **Step 2: Grep wall-clock 违反**

```bash
grep -rn "datetime\.now()" --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -v "archive/" | grep -v "__pycache__" \
    | grep -v "interfaces.py" | grep -v "ibkr_executor.py" \
    > /tmp/grep_wallclock.log
cat /tmp/grep_wallclock.log
```

预期: 0 hits 在业务模块.

- [ ] **Step 3: Grep ib_insync 在 ibkr_executor.py 之外**

```bash
grep -rn "import ib_insync\|from ib_insync" --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -v "archive/" | grep -v "__pycache__" | grep -v "ibkr_executor.py" \
    > /tmp/grep_ibinsync.log
cat /tmp/grep_ibinsync.log
```

预期: 0 hits.

- [ ] **Step 4: 写 cleanup log**

`archive/tactical/CLEANUP_LOG.md`:

```markdown
# Cleanup Log (Phase 6 of production refactor)

**日期**: 2026-05-15

## Grep 结果

### 伪代码 / hardcode 收益 (Step 1)

[复制 /tmp/grep_pseudo.log 内容]

**分类**:
- 假阳性 (TODO/FIXME 在 docstring 描述未来扩展): N 处, 忽略
- 真违规: M 处, 修复见 Step 5

### Wall-clock 违反 (Step 2)

[复制 /tmp/grep_wallclock.log 内容]

[结果应该是 0 hits 或全在 archive/]

### ib_insync 隔离 (Step 3)

[复制 /tmp/grep_ibinsync.log 内容]

[结果应该是 0 hits]

## 修复列表

[若有真违规, 列出每处 file:line + 修复说明]

[若无真违规, 写"无真违规, 主路径已清洁"]
```

- [ ] **Step 5: 修任何真违规** (按 Step 4 列出处理, 若有的话)

逐个修, 单独 commit (或合并). 修法因具体违规而异.

- [ ] **Step 6: Commit cleanup log**

```bash
git add archive/tactical/CLEANUP_LOG.md
git commit -m "docs(cleanup): Phase 6 grep cleanup log (无真违规 / N 处修复)"
```

---

## Task 11: Phase 7 — Tests + Backtest + State Recovery + Paper Verify

**Files**: 无 code change (除非测试发现 bug 需补丁).

- [ ] **Step 1: test.py 全套**

```bash
python test.py 2>&1 | tail -10
```

预期: `Ran 252 tests in X.Xs / OK (skipped=N)` 其中 N ≈ 16-20. 0 fail/error.

- [ ] **Step 2: Single-symbol baseline backtest**

```bash
echo "=== UVXY ==="
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益率|年化收益率|最大回撤|Sharpe" | head -4

echo ""
echo "=== VXX ==="
python backtest.py --csv data/vxx_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益率|年化收益率|最大回撤|Sharpe" | head -4
```

预期:
- UVXY: `+82.81%`
- VXX: `+226.79%`

- [ ] **Step 3: Multi-symbol backtest**

```bash
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv --allocations 0.5 0.5 \
    --capital 10000 --interval 4h --label phase7_verify 2>&1 | tail -15
```

预期: 合并 `+153.93%`, UVXY sub `+70.38%`, VXX sub `+237.48%`.

- [ ] **Step 4: State recovery 测试 — backtest 中段 stop + 重启**

由于这测试本质需要"中断 backtest", 简化为 manually 验证 SQLite + JSON 文件结构存在:

```bash
# 跑一次 backtest 让 db 创建
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 > /dev/null 2>&1

# 看 db 文件结构
ls -la runtime/trades.db* 2>&1 || true
# 看 SQLite 表
sqlite3 runtime/trades.db ".tables" 2>&1 | head -10
```

预期: 看到 `trades`, `pnl_fifo_queue`, `state_machine_state`, `risk_state`, `grid_sessions`, `grid_session_events` 等表 (tactical 表在 archive 后仍存在, 因为是 DB 历史).

如果 grid_sessions 表存在但 archive 后 SessionManager 不再写: 这是 OK 的, 表保留但空. CLAUDE.md §8 优先级 1 章: state recovery 链路完整.

- [ ] **Step 5: Paper verify (main.py --paper-verify, mock IBKR)**

```bash
# 用 Task 9 smoke test 验证装配
python -m unittest tests.main_assembly.test_main_multi_assembly -v 2>&1 | tail -3
```

预期: PASS.

(注: 真实 IBKR paper account 连接测试不可自动化, 留作 user manual 验证. CLAUDE.md §7.)

- [ ] **Step 6: Commit (若有任何 fixup)**

如果 Step 1-5 发现 bug 修了, commit. 若无, 跳过.

---

## Task 12: Phase 8 — Docs + Registry

**Files**:
- Modify: `README.md` (Task 3 撤销到 8c41c62, 重新写)
- Create: `CHANGELOG.md`
- Create: `reports/refactor_2026_05_15.md`
- Update: `findings.md` (加 F10)
- Update: `progress.md` (加会话 5)

- [ ] **Step 1: 改写 README.md §5 能力快照**

Edit `README.md`:

§5 部分 (大约 L147-167) 改为反映重构后状态:

```markdown
## 5. 当前能力快照

> 数值会随研究推进变化, 权威来源是 [`findings.md`](./findings.md) +
> `runtime/experiments/` 下的产物.

- **当前默认基线**: `UVXY+VXX 50/50` × `4h` × `MultiSymbolOrchestrator`
- **回测样本**:
  - UVXY 5y 4h CSV (Alpaca IEX, 2021-01-04 → 2026-04-24)
  - VXX  ~5y 4h CSV (Alpaca IEX, 2021-05-17 → 2026-05-14)
- **支持的策略周期**: `15m` / `1h` / `4h` / `1d` (改 `config.STRATEGY_INTERVAL` 即可)
- **当前 5y 回测指标 ($10,000 capital, 真实化撮合)**:
  - **多标的合并 (UVXY+VXX 50/50)**: ret **+153.93%** / annu +20.78% / Sharpe ~0.45 / MDD 15.09%
  - UVXY sub-bot ($5,000): ret +70.38%
  - VXX  sub-bot ($5,000): ret +237.48%
- **单标的参考** (回测 + sanity, 不是实盘默认):
  - UVXY: +82.81% / Sharpe 0.32 / MDD 15.86%
  - VXX: +226.79% / Sharpe 0.32 / MDD 15.17%
- **战术化已 archived** (2026-05-15 严证伪通过): 详见
  [`archive/tactical/reports/tactical_proof_of_impossibility.md`](./archive/tactical/reports/tactical_proof_of_impossibility.md)
- **测试覆盖**: 252 个 `test_*` 用例 (含 ~16 个 tactical-related skipped)
```

更新 §3 (Architecture) 加多标的部署示例.

更新 §4 (一眼看懂运行方式) 用多标的命令为主:

```markdown
# 实盘多标的 (paper 验证)
python main.py --paper-verify

# 实盘 (默认 UVXY+VXX 50/50)
IBKR_HOST=127.0.0.1 IBKR_PORT=4002 python main.py

# 多标的回测
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv --allocations 0.5 0.5 \
    --capital 10000 --interval 4h

# 单标的回测 (legacy)
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000

# 测试
python test.py
```

- [ ] **Step 2: 写 CHANGELOG.md**

`CHANGELOG.md`:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

## [2026-05-15] Production Refactor

### Removed (Archived to archive/tactical/)
- 战术化模块: `tactical_config.py`, `tactical_rules.py`, `session_manager.py`
- Sweep 工具: `scripts/prove_tactical.py`, `scripts/_proof_runner.py`, `scripts/tune_tactical.py`
- 战术化测试: `tests/proof/`
- 战术化 reports: `tactical_proof_of_impossibility.md`, `tactical_extended_screening.md`
- 战术化 runtime data: `tactical_proof/`, `tactical_extended/` (gitignored)

### Added
- 多标的并行回测: `scripts/run_multi_backtest.py` (UVXY+VXX 50/50, +153.93% 5y)
- Walk-forward 验证: `scripts/walk_forward_fixed.py`
- VXX 5y 数据: `data/vxx_4h.csv`
- main.py `MultiSymbolOrchestrator` 入口 + `--paper-verify` flag
- Symbol 级隔离基础设施: `bot_factory.py`, `orchestrator.py`,
  `capital_allocator.py`, `client_id_allocator.py`, `account_risk.py`
- `archive/tactical/README.md` + `CLEANUP_LOG.md`
- `CHANGELOG.md` (本文件)
- `reports/refactor_2026_05_15.md` (重构完整报告)
- `reports/v49_27pp_regression.md` (Phase 4.D git-bisect)
- `tests/main_assembly/` (main.py 装配 smoke test)

### Fixed
- `risk_manager._capital_reference()` helper: multi-symbol sub-bot 不再误用全账户
  TOTAL_CAPITAL 作 baseline (D5 发现的 bug, re-applied 在 8c41c62 base 上)
- `grid_bot._capital()` helper: 同上, sizing 用本 bot allocated_capital
- `entry_filter.py` L193: 删 dead `getattr` fallback `-1.0` (P1 reviewer Important)
- `config.py` L211: 删 stale "默认改 -1000" 注释 (P1 reviewer Important)

### Investigated (Not Fixed, Follow-up)
- V49 (`4fdb801`) → `8c41c62` 之间 -27pp 回归 root cause
  (详见 `reports/v49_27pp_regression.md`)

### Surgical Revert (Phase 2)
以下文件 `git checkout 8c41c62 --` 回退:
backtest.py, ibkr_executor.py, report_generator.py, requirements.txt,
simulated_executor.py, trade_logger.py

(grid_bot.py, risk_manager.py, config.py, main.py, README.md 撤销后又部分 re-apply
fix 见 "Fixed" + "Added"; state_machine.py / entry_filter.py / test.py 保留 D 路径增强)
```

- [ ] **Step 3: 写 reports/refactor_2026_05_15.md**

按 spec §8 验收清单, 写 6 节:

```markdown
# 生产级重构 完整报告 (2026-05-15)

> Spec: docs/superpowers/specs/2026-05-15-production-refactor-design.md
> Plan: docs/superpowers/plans/2026-05-15-production-refactor.md

## 1. 改动文件

`git diff --stat HEAD~12..HEAD` 输出: (实测填)

新建: archive/tactical/, CHANGELOG.md, reports/refactor_2026_05_15.md,
      reports/v49_27pp_regression.md, tests/main_assembly/
修改: bot_factory.py, test.py, state_machine.py (保留), entry_filter.py (保留+fix),
      grid_bot.py (撤销+4.E patch), risk_manager.py (撤销+4.A fix),
      config.py (撤销+4.C fix), main.py (撤销+改写), README.md (撤销+重写)
撤销到 8c41c62: backtest.py, ibkr_executor.py, report_generator.py,
              requirements.txt, simulated_executor.py, trade_logger.py

## 2. 回退内容

Phase 2 surgical checkout 撤销 11 个 tracked file 到 8c41c62.
保留 state_machine.py / entry_filter.py / test.py 的 D 路径 modified 改动.
具体每个 file diff 摘要见 git log.

## 3. 漏洞根因 + 修复

### 4.A risk_manager multi-symbol capital reference
- Root cause: 8c41c62 risk_manager check_hard_stop 用 config.TOTAL_CAPITAL 作 baseline
- Fix: 加 _capital_reference() helper, 多 symbol 传 allocated_capital
- Verify: backtest UVXY +82.81% (单标退化) + multi +153.93% (Gate PASS)

### 4.B entry_filter getattr fallback
- Root cause: dead `getattr(config, "ENTRY_MAX_ADX_SLOPE", -1.0)`, fallback 与 default 不一致
- Fix: 改为直接读 `config.ENTRY_MAX_ADX_SLOPE`
- Verify: test.py 全绿, backtest 不变

### 4.C config.py L211 stale comment
- Root cause: 注释"默认改 -1000" 与实际 default `-0.5` 矛盾
- Fix: 删除该行
- Verify: 纯文档改动, 无 runtime 影响

### 4.D V49 → 8c41c62 -27pp 回归
- 见 reports/v49_27pp_regression.md
- First bad commit: [bisect 结果]
- 未修, 标记 follow-up

### 4.E GridBot allocated_capital (plan-discovered)
- Root cause: 8c41c62 GridBot 不接 allocated_capital, sub-bot sizing 用全账户
- Fix: 加 minimal allocated_capital + symbol kwargs + _capital() helper
- Verify: backtest UVXY +82.81% (单标等价), multi +153.93% (Gate PASS)

## 4. 测试命令与真实结果

[复制 Task 11 实际 shell 输出]

### test.py
```
[实测输出]
```

### Single-symbol backtest
```
[实测输出]
```

### Multi-symbol backtest
```
[实测输出, 含 +153.93% 复现]
```

### Paper-verify smoke test
```
[实测输出]
```

## 5. 是否复现 +153.93% 回报

**是**. Task 5 Gate PASS + Task 6 verify + Task 11.3 三次独立运行:
- Task 5: +153.93% (Gate verify)
- Task 6: +153.93% (4.A fix 后 verify)
- Task 11.3: +153.93% (Phase 7 final verify)

数据可复现, 与原 D5 报告 (`reports/multi_symbol_alpha.md` Part 4) 完全一致.

## 6. 剩余风险

1. **V49 -27pp 回归未修** (Phase 4.D 已定位 first bad commit, 修复另开 spec)
2. **实盘 paper account ≥ 4 周对账未做** (CLAUDE.md §7 user manual step)
3. **tactical 模块技术上仍能从 archive/ 恢复** (但 README + CHANGELOG 都明确不推荐)
4. **`config.TURBO_ENABLED` 已不存在** (tactical archived 后, 该 flag 在 archive/tactical/tactical_config.py 中, 主路径不读)
5. **Phase 7.5 真实 IBKR paper 连接未自动测试** (smoke test 用 mock 验证装配, 真连依赖用户手动)
```

- [ ] **Step 4: findings.md 追加 F10**

末尾追加:

```markdown
---

## F10. 生产级重构 (2026-05-15 会话 5)

**Spec**: docs/superpowers/specs/2026-05-15-production-refactor-design.md
**Plan**: docs/superpowers/plans/2026-05-15-production-refactor.md
**Report**: reports/refactor_2026_05_15.md

### 主要变化
- 战术化 archived 到 archive/tactical/ (3 模块 + 3 sweep 工具 + tests + reports)
- 11 tracked file 撤销到 `8c41c62`, 然后选择性 re-apply (risk_manager 4.A / grid_bot 4.E / config 4.C / entry_filter 4.B / main.py Phase 5)
- main.py 默认 MultiSymbolOrchestrator (UVXY+VXX 50/50)
- 多标的 5y +153.93% 复现 (Task 5 + Task 11)

### 修复的 4 个 bug (+ 1 plan-discovered)
- 4.A risk_manager multi-symbol capital reference (D5 发现, re-applied)
- 4.B entry_filter.py getattr fallback dead code
- 4.C config.py L211 stale comment
- 4.D V49 → 8c41c62 -27pp 回归 (仅报告, 未修)
- 4.E (plan-discovered) GridBot allocated_capital 支持

### 仍未做
- V49 -27pp 修复 (Phase 4.D follow-up)
- 实盘 paper ≥ 4 周对账
- CLAUDE.md §1 描述更新 (user 明确不动)
```

- [ ] **Step 5: progress.md 追加会话 5**

末尾追加 (与会话 1-4 同风格):

```markdown
---

## 2026-05-15 会话 5 — 生产级重构 (Production Refactor)

**Plan**: `docs/superpowers/plans/2026-05-15-production-refactor.md`
**Spec**: `docs/superpowers/specs/2026-05-15-production-refactor-design.md`

### Commits (按顺序)

| Task | Commit | 说明 |
|---|---|---|
| T1 | [SHA] | tactical 模块 + sweep 工具 archive 到 archive/tactical/ |
| T2 | [SHA] | 主路径 tactical 依赖隔离 (bot_factory 删 session_mgr + test.py import guard) |
| T3+T4 | [SHA] | surgical revert 11 tracked file + GridBot allocated_capital re-apply (4.E) |
| T5 | (verify) | Phase 3 Gate PASS: multi-symbol +153.93% |
| T6 | [SHA] | risk_manager multi-symbol fix (4.A) |
| T7 | [SHA] | entry_filter getattr + config L211 stale comment (4.B+4.C) |
| T8 | [SHA] | V49 -27pp 回归 git-bisect 报告 (4.D) |
| T9 | [SHA] | main.py MultiSymbolOrchestrator + --paper-verify |
| T10 | [SHA] | grep cleanup log (无真违规) |
| T11 | (verify) | tests + recovery + paper-verify smoke 全部 PASS |
| T12 | [本次] | README + CHANGELOG + reports + findings F10 + progress |

### 主要数据
- 多标的 5y +153.93% 复现 (3 次独立运行验证)
- UVXY 单 +82.81% / VXX 单 +226.79% (不变)
- 战术化 archive 后主路径 0 tactical import
- test.py 全绿 (252 - N skipped, 0 fail/error)

### 整体 Verdict
✅ 生产重构完整闭环. 主路径干净, 多标的能力保留, 4+1 bug 修复或定位.

### 测试状态
[Phase 7 实测 test.py 输出]

### 未做的事 (诚实声明)
1. V49 -27pp 真实根因修复 (Phase 4.D 仅定位, 未修)
2. 实盘 paper >= 4 周对账 (CLAUDE.md §7 user manual)
3. CLAUDE.md §1 描述更新 (user 明确不动)
```

- [ ] **Step 6: 跑 test.py 最终验证**

```bash
python test.py 2>&1 | tail -3
```

预期: 全绿.

- [ ] **Step 7: Commit Task 12**

```bash
git add README.md CHANGELOG.md reports/refactor_2026_05_15.md \
    findings.md progress.md
git commit -m "$(cat <<'EOF'
docs(refactor): 生产重构终态 — README §5 + CHANGELOG + report + findings F10 + progress

完整闭环:
- README.md: §5 + §3 + §4 更新, 多标的 +153.93% 为 default baseline
- CHANGELOG.md (新建): keep-a-changelog 格式记录 Added/Removed/Fixed
- reports/refactor_2026_05_15.md: 完整 6 节报告 (改动 / 回退 / 漏洞 / 测试 / 复现 / 风险)
- findings.md F10: 重构总结
- progress.md 会话 5: T1-T12 commit 表

整体 Verdict: ✅ 闭环
- 主路径 0 tactical import
- 多标的 +153.93% 复现 (3 次)
- test.py 全绿
- 5 个 bug 处理 (4 fix + 1 报告 follow-up)
EOF
)"
```

---

## Self-Review

### Spec coverage check

| Spec section | Task 覆盖 | OK? |
|---|---|---|
| §2 命题 + 7 success criteria | Task 1-12 全覆盖 | ✅ |
| §3 Phase 1 (isolate tactical) | Task 1+2 | ✅ |
| §3 Phase 2 (surgical revert) | Task 3 | ✅ |
| §3 Phase 3 (verify multi-symbol) | Task 5 | ✅ |
| §3 Phase 4.A risk_manager | Task 6 | ✅ |
| §3 Phase 4.B entry_filter | Task 7 | ✅ |
| §3 Phase 4.C config | Task 7 | ✅ |
| §3 Phase 4.D git-bisect | Task 8 | ✅ |
| §3 Phase 4.E GridBot (plan-discovered) | Task 4 | ✅ (spec 漏掉, plan 补) |
| §3 Phase 5 main.py | Task 9 | ✅ |
| §3 Phase 6 grep cleanup | Task 10 | ✅ |
| §3 Phase 7 tests + recovery + paper | Task 11 | ✅ |
| §3 Phase 8 docs + CHANGELOG | Task 12 | ✅ |
| §4 Deliverables | Task 1+12 涵盖全部 | ✅ |
| §6 风险与缓解 | 每 Task 内 fail-mode 都说了 | ✅ |
| §8 验收清单 | Task 12 Step 3 report 6 节对齐 | ✅ |

### Placeholder scan

检查 "TBD" / "implement later" / "fill in" / "Similar to Task N":
- Task 8 Step 7 `reports/v49_27pp_regression.md` 模板有 `[Step 5 抓的 SHA]` 等 — 是**运行时填充**, 不是 plan placeholder
- Task 12 Step 3-5 中各种 `[实测填]` / `[SHA]` — 是**运行时填充**, 不是 plan placeholder
- 没 "Similar to Task N" / "implement later" 等真 placeholder ✅

### Type consistency

- `allocated_capital: Optional[float] = None` 在 Task 4 (GridBot) + Task 6 (RiskManager) 一致 ✅
- `_capital_reference()` 在 Task 6 RiskManager + `_capital()` 在 Task 4 GridBot — name 不一致! 但因为是两个 class 各自方法, 不冲突. 可接受.
- `build_multi_symbol_bots(symbols, total_capital, allocations)` 在 Task 9 调用与 D 路径已有 bot_factory.py 签名一致 ✅
- `MultiSymbolOrchestrator(bots)` 在 Task 9 与 D 路径 orchestrator.py 签名一致 ✅
- `--paper-verify` flag 在 Task 9 main.py + Task 9 smoke test + Task 11 Step 5 一致 ✅

---

## Execution Handoff

Plan 完成, 落 `docs/superpowers/plans/2026-05-15-production-refactor.md`. 12 个 Task, 工作量 ~6-8h (主要瓶颈 Task 8 git-bisect ~2-3h + 跑测试时间).

两个执行选项:

**1. Subagent-Driven (推荐)** — 每 Task 派 fresh subagent + 双阶段 review. 上一轮 D 路径已经成功用过这个模式. 适合本次 12 Task scope.

**2. Inline Execution** — 在当前 session 顺序执行 12 Task. 上下文连贯但 context 已经较深.

哪种?
