# 生产级重构 — 去战术化 + 回退稳定网格 + 修漏 + 多标的实盘 — Design Spec

> **日期**: 2026-05-15
> **状态**: design (待用户 review)
> **关联**:
> - `findings.md` F1-F9
> - `reports/tactical_proof_of_impossibility.md` (战术化严证伪)
> - `reports/multi_symbol_alpha.md` (多标的 D 路径)
> - `docs/superpowers/specs/2026-05-15-multi-symbol-alpha-design.md`

---

## 1. 背景

当前 HEAD `67cd67d` 含 D 路径完整成果 (P1-P10 + E1-E7 + D1-D6 三轮工作累积 28+ commits, 主要 commits 在 GitHub 上 `main` HEAD `8c41c62` **未上传**). 主要状态:
- 多标的能力已经验证 (UVXY+VXX 50/50 5y +153.93%, fact-checked 复现, 无 hardcode/伪代码)
- 战术化严证伪通过, `config.TURBO_ENABLED` 默认 OFF, EXPERIMENTAL banner 在位
- 但**战术化模块文件 (session_manager.py / tactical_rules.py / tactical_config.py) 仍在主路径**, 与 user "去战术化"目标冲突
- 多个 untracked 关键文件 (bot_factory.py / orchestrator.py / capital_allocator.py / client_id_allocator.py / account_risk.py) 一直在 working tree, 没 commit
- 4 个已知 bug:
  - A: D5 发现的 risk_manager multi-symbol capital reference bug (已修在 76ebb4d, 但 8c41c62 状态没有此修)
  - B: entry_filter.py `getattr(config, "ENTRY_MAX_ADX_SLOPE", -1.0)` 与 config default `-0.5` 不一致 (dead code 但 stale, P1 reviewer 提)
  - C: config.py L209 stale "默认改 -1000" 注释 (实际 default -0.5)
  - D: V49 (4fdb801) → 8c41c62 之间 -27pp 回归, 14 个 commit, 未定位

用户目标: 把代码库重构成"干净的多标的网格生产系统" — 去战术化, 回退稳定基线, 保留多标的, 修 4 bug, main.py 默认多标的实盘, 全过程无伪代码 / 无 mock / 无 hardcode.

---

## 2. 命题

把 tracked file 状态对齐 GitHub main HEAD (`8c41c62`), **除了** state_machine.py + entry_filter.py + test.py (保留 D 路径增强); 战术模块 + sweep 工具集中到 `archive/tactical/`; 多标的能力 (UVXY+VXX 50/50) 必须能复现 `+153.93%` (fact-checked); 修 4 个 bug (A/B/C/D); main.py 默认走 `MultiSymbolOrchestrator`; 全过程**禁伪代码/硬编码/mock 成功/always pass/绕过验证**.

**整体成功标准**:
1. 主路径 grep `import tactical|from tactical|session_manager|tactical_rules` 返回 0 hits (除 archive/ 之外)
2. `python scripts/run_multi_backtest.py --symbols UVXY VXX --allocations 0.5 0.5 ...` 输出 `+153.93%` (±0.01pp)
3. `python backtest.py --csv data/uvxy_4h.csv ...` 输出 `+82.81%` (±0.01pp), VXX 输出 `+226.79%`
4. `python test.py` 全绿 (含 ImportSkip 的 tactical test, 但不 fail)
5. 4 bug 全部 root cause + minimal fix + 验证
6. main.py 在 IBKR paper 端口启动测试: 双 client_id 独立连接, 资金按 allocations 切, 优雅 shutdown
7. CHANGELOG / README §5 更新 + reports/refactor_2026_05_15.md + reports/v49_27pp_regression.md 全部 commit

**任何"已修复 / 已验证 / 已通过"必须基于实际 shell 命令输出, 不允许 LLM-generated 假装结果.**

---

## 3. 八 Phase 设计

注: spec self-review 期间发现原"七 phase" 中 Phase 6.5 paper test 依赖 main.py 改写 (原在 Phase 7), 顺序错. 重排为 8 phase: Phase 1-4 不变, 新增 Phase 5 (main.py 改写), 原 Phase 5/6/7 → Phase 6/7/8.

### Phase 1 — Scan + Isolate Tactical (~30 min)

#### 1.1 Grep tactical 调用面

```bash
grep -rn "import tactical\|from tactical\|import session_manager\|from session_manager\|import tactical_rules\|from tactical_rules" \
    --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -v "archive/" | grep -v "__pycache__"
```

记录所有 hits 到 `archive/tactical/USAGE_BEFORE_ISOLATION.md`. 这些是 Phase 1.3 处理依赖的输入.

#### 1.2 移文件到 archive/tactical/

```
session_manager.py             → archive/tactical/session_manager.py
tactical_rules.py              → archive/tactical/tactical_rules.py
tactical_config.py             → archive/tactical/tactical_config.py
scripts/_proof_runner.py       → archive/tactical/scripts/_proof_runner.py
scripts/prove_tactical.py      → archive/tactical/scripts/prove_tactical.py
scripts/tune_tactical.py       → archive/tactical/scripts/tune_tactical.py
tests/proof/                   → archive/tactical/tests/proof/
reports/tactical_proof_of_impossibility.md  → archive/tactical/reports/
reports/tactical_extended_screening.md      → archive/tactical/reports/
runtime/experiments/tactical_proof/         → archive/tactical/runtime/tactical_proof/
runtime/experiments/tactical_extended/      → archive/tactical/runtime/tactical_extended/
```

注: runtime 在 .gitignore, 移动只为 disk 清理. 不会进 git.

**保留主路径** (用户选 tactical 边界 B):
- state_machine.py 6 状态扩展 (含 OFFENSIVE_GRID / DEFENSIVE_GRID / COOLDOWN)
- entry_filter.py T1 ADX_slope + S3 recent_range
- screen_symbols.py (ETF 筛选, 不是 tactical)

#### 1.3 处理主路径残留依赖

- **grid_bot.py 战术分支**: Phase 2 撤销到 `8c41c62` 时该文件本来就**没有** session_manager / tactical_rules import. 撤销自动消除依赖. 不需要单独处理.
- **test.py 中 TestTacticalActionsReachable + _patch_tactical_on**: 因为 archive/tactical 不在 sys.path, 这 16 个相关 test 会 ImportError. **处理**: 在 test class setUp 用 `try: import; except ImportError: self.skipTest("tactical archived")` 把这部分 test 标记 skip (代码保留作历史, 不阻塞).

#### 1.4 Verify imports clean

```bash
python -c "import grid_bot; import backtest; import main; import risk_manager; print('OK')"
```

期望: `OK`. 主路径任何 module 不应再 import tactical_*.

### Phase 2 — Surgical Revert Tracked Files (~20 min)

#### 2.1 Define preserve list

**保留 modified (D 路径增强不丢)**:
- `state_machine.py`
- `entry_filter.py`
- `test.py`

**撤销到 `8c41c62`**:
- `backtest.py`
- `grid_bot.py`
- `ibkr_executor.py`
- `main.py`
- `report_generator.py`
- `requirements.txt`
- `risk_manager.py`
- `simulated_executor.py`
- `trade_logger.py`
- `config.py`
- `README.md`

#### 2.2 Execute surgical checkout

```bash
git checkout 8c41c62 -- backtest.py grid_bot.py ibkr_executor.py main.py \
    report_generator.py requirements.txt risk_manager.py simulated_executor.py \
    trade_logger.py config.py README.md
```

#### 2.3 验证状态

```bash
git status
git diff --stat HEAD
```

期望: 撤销的 11 个文件 unmodified, state_machine.py / entry_filter.py / test.py 仍 modified, untracked 文件 (session_manager.py 等) 还在 working tree (Phase 1 之后已移到 archive/).

#### 2.4 后续 re-apply (Phase 4 内做)

- `config.py` BACKTEST_DEFAULT_CAPITAL → 10000 (Phase 4 内)
- `config.py` ENTRY_MAX_WAIT_BARS env override 默认 → 12 (Phase 4 内, 但**注**: D3 实测对回测无影响, 仍 apply 以维持 walk-forward 一致性)
- `risk_manager.py` D5 multi-symbol fix (Phase 4.A)
- `config.py` TURBO_ENABLED default → "0" (Phase 4 内, 因为 tactical 已 archive, 这个常量本身可保留 default OFF; archive 不依赖)

### Phase 3 — Verify Multi-Symbol Intact (~10 min)

跑 multi-symbol backtest **精确复现 +153.93%**:

```bash
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv --allocations 0.5 0.5 \
    --capital 10000 --interval 4h --label phase3_verify
```

**Gate**: `total_ret_pct == 153.93` (±0.01pp 容差).

**Phase 3 Gate FAIL 处理**: 必须找出原因 (Phase 2 撤销时漏保留某文件, 或某 untracked 依赖被 Phase 1 移动). 不允许 LLM "差不多就行" 的 fudge factor. 找到 + 修 + 重跑.

### Phase 4 — 修 4 Bug (顺序 A → B → C → D, ~3-4h)

#### 4.A risk_manager multi-symbol capital reference fix (~20 min)

**Root cause** (D5 已诊断):
- `check_hard_stop` / `check_position_limit` 用 `config.TOTAL_CAPITAL` 作 baseline
- multi-symbol 装配时 sub-bot allocated_capital ($5k) << TOTAL_CAPITAL ($10k)
- 启动时 (10000 - 5000) / 10000 = 50% > HARD_STOP_LOSS_PCT (20%) → 触发立即清算

**Minimal fix** (在 8c41c62 risk_manager.py base 上**手写实现**, 不 cherry-pick D5 commit 76ebb4d):
- 加 `_capital_reference()` helper, 接受 optional `allocated_capital` kwarg
- 多 symbol 时返回 allocated, 单 symbol 时 fallback `require_total_capital()`
- 改 `check_hard_stop` / `check_position_limit` 用 `_capital_reference()`
- RiskManager.__init__ 加 `allocated_capital: Optional[float] = None` 参数 (向后兼容)
- **不 cherry-pick** 因为 cherry-pick 可能引入 D5 commit 时间段的其他 risk_manager 改动 (account_risk 集成, capital_provider 等), 这些可能与 8c41c62 base 不兼容. 手写隔离最干净.

**Verify**:
- Phase 3 verify pass = 隐式确认 (+153.93% 成立需要这个 fix)
- test.py 全绿 (single-symbol 退化等价, D5 已验证 delta < 0.01pp)
- 额外: `python backtest.py --csv data/uvxy_4h.csv ... → +82.81%` (single-symbol 不变)

#### 4.B entry_filter getattr fallback fix (~15 min)

**Root cause**: P1 code reviewer 指出, `entry_filter.py` 内
```python
max_slope = getattr(config, "ENTRY_MAX_ADX_SLOPE", -1.0)
```
fallback `-1.0` 与 config default `-0.5` 不一致. 因为 config.ENTRY_MAX_ADX_SLOPE 必存在, fallback 永不触发 (dead code). 但 stale 值会误导未来读者.

**Minimal fix**: 删除 getattr, 直接 `config.ENTRY_MAX_ADX_SLOPE`.

**Verify**:
- test.py 全绿
- backtest UVXY 单标 +82.81% 复现 (因为 getattr 是 dead code, 改不改不影响 runtime)

#### 4.C config.py L209 stale comment fix (~5 min)

**Root cause**: P1 code reviewer 指出, config.py L209 注释:
> `默认改 -1000 (关闭)`

但实际 L219:
```python
os.getenv("ENTRY_MAX_ADX_SLOPE", "-0.5")
```

Default 是 `-0.5`, 不是 `-1000`. 注释过时.

**Minimal fix**: 删除该注释行, 或改为反映实际 default `-0.5`.

**Verify**: 无 (纯文档改动). test.py 全绿确认无破坏.

#### 4.D git-bisect V49 → 8c41c62 -27pp 回归 (~2-3 h)

**Root cause investigation** (不是 minimal fix, 只是定位):

Bisect 范围: 14 commits (4fdb801 inclusive → 8c41c62 inclusive)
```
81d3c6a 统计口径修复 + clock 注入收紧 + 新增交易次数统计
ee1fa2f Delete CLAUDE.md
bef6e87 Delete data/qqq.py
f85fdd6 Delete PROJECT_STATUS.md
c4179d6 ibkr 取价 retry + waiting_entry 自动恢复 + 入场窗口收紧
ff4fdbf Change ENTRY_MAX_WAIT_BARS from 12 to 1
7e12326 ENTRY_MAX_WAIT_BARS 12 → 1
a0d082a WAITING_ENTRY 处理顺序修复 + 超时浮点边界保护
e0d0314 ENTRY_MAX_WAIT_BARS 默认 1 → 1.5
e369447 WAITING_ENTRY 节拍与 STRATEGY_INTERVAL 解耦 + scanning→waiting 立即评估 + 非交易时段不消耗窗口
8c41c62 daily_snapshots 写入时机解耦
```

**Bisect 方法**:
- `git stash --include-untracked` 全部 stash 干净 working tree
- `git bisect start; git bisect bad 8c41c62; git bisect good 4fdb801`
- 每个候选 commit 跑 `python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000` (注: 此时 config.py 已在 4fdb801 状态, capital=2000 default, 显式 --capital 10000 覆盖)
- 判 ret > +100% (good) 或 < +90% (bad)
- 预期 4-5 次 bisect step, 找出第一个 bad commit
- `git bisect reset` 恢复 main, `git stash pop` 恢复 working tree

**输出**: `reports/v49_27pp_regression.md` 含:
- 14 commits 列表 + bisect 步骤详细
- 每步 backtest ret + good/bad 判定
- 找到的 first bad commit + diff 摘要 + root cause 分析
- 推荐: 是否值得回滚该 commit (评估其他副作用) 或留作 follow-up

**不做**: 不强制修. 只**报告**. 修复是另一个独立 spec.

### Phase 5 — main.py MultiSymbolOrchestrator 改写 (~45 min)

**前置**: Phase 2 已撤销 main.py 到 `8c41c62` 状态 (单标的 IBKR 装配). Phase 4 已修 risk_manager 让 multi-symbol 装配能跑.

#### 5.1 改写 main.py 入口

`8c41c62` main.py 主流程:
```python
# 大约结构
import config
from grid_bot import GridBot
from ibkr_executor import IBKRExecutor
# ... 单标的装配 ...
bot = GridBot(...)
bot.start()
while not bot.should_stop():
    bot.step()
    time.sleep(config.scanning_interval_sec())
bot.shutdown()
```

改写为 (使用现有 untracked `bot_factory.build_multi_symbol_bots` + `orchestrator.MultiSymbolOrchestrator`):

```python
import argparse
import config
from bot_factory import build_multi_symbol_bots
from orchestrator import MultiSymbolOrchestrator
# ... IBKR NetLiquidation 注入 config.TOTAL_CAPITAL ...

DEFAULT_SYMBOLS = ["UVXY", "VXX"]
DEFAULT_ALLOCATIONS = {"UVXY": 0.5, "VXX": 0.5}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--paper-verify", action="store_true",
                        help="只验证装配 + 一次 step, 不实际下单, 然后 shutdown")
    args = parser.parse_args()

    # NetLiquidation 注入 (CLAUDE.md §5.3)
    # ... 现有 8c41c62 main.py 的 NetLiquidation 注入逻辑保留, 改用 multi-symbol allocator ...

    bots = build_multi_symbol_bots(
        symbols=args.symbols,
        total_capital=config.TOTAL_CAPITAL,
        allocations=DEFAULT_ALLOCATIONS,
    )
    orch = MultiSymbolOrchestrator(bots)
    orch.start_all()

    if args.paper_verify:
        # Dry-run: 一次 step + shutdown, 不实际下单
        orch.step_all()
        orch.shutdown_all()
        print("✅ paper-verify pass: 装配 + 一次 step + shutdown 全部 OK")
        return

    while not orch.should_stop_all():
        orch.step_all()
        time.sleep(orch.next_sleep_sec())
    orch.shutdown_all()
```

#### 5.2 验证装配能起来 (不连 IBKR, 不依赖 paper account)

由于 IBKR Gateway 可能没起, **单元级**验证 main.py 装配逻辑用 Mock IBKR. 加 test:

```python
# tests/main_assembly/test_main_multi_assembly.py
def test_main_assembly_with_mock_ibkr():
    """验证 main.py 用 build_multi_symbol_bots 装配, 不实际连 IBKR."""
    # mock IBKRExecutor.connect / get_account_summary 返回 fake 数据
    # 走 main.main(['--paper-verify']) 应该 print '✅ paper-verify pass'
```

#### 5.3 Commit Phase 5

```bash
git add main.py tests/main_assembly/
git commit -m "feat(main): 改写为 MultiSymbolOrchestrator 入口 + --paper-verify dry-run flag"
```

### Phase 6 — Search + Clean Pseudocode (~20 min)

**Grep 主路径** (排除 archive/ / __pycache__/ / tests/ / reports/ / docs/):

```bash
# 伪代码 / 假成功
grep -rnE "TODO|FIXME|XXX|HACK|placeholder|always.*pass|mock_success|fake_ret|hardcoded.*ret|return 153|return 82\.81|return 226\.79" \
    --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -v "archive/" | grep -v "__pycache__" | grep -v "/tests/" \
    | grep -v "/reports/" | grep -v "/docs/"

# Wall-clock 违反 CLAUDE.md §9
grep -rn "datetime\.now()" --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -v "archive/" | grep -v "__pycache__" \
    | grep -v "interfaces.py" | grep -v "ibkr_executor.py"

# ib_insync 在 ibkr_executor.py 之外
grep -rn "import ib_insync\|from ib_insync" --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -v "archive/" | grep -v "__pycache__" | grep -v "ibkr_executor.py"
```

**所有 hits 处理**:
- 假阳性 (例如 "TODO" 在 docstring 中描述未来扩展) — 忽略, 记录到 archive/CLEANUP_LOG.md
- 真违规 — 修复 + 单独 commit + log

期望 (基于先前 grep): 0 真违规, 因为 entry_filter datetime.now() 在 P1 已经修了, 其他业务模块也清理过.

### Phase 7 — Run Tests + Backtest + State Recovery + Paper Verify (~30 min)

#### 7.1 test.py 全绿

```bash
python test.py 2>&1 | tail -10
```

期望: `Ran N tests in X.Xs / OK` 其中 N = 252 (含 skipped tactical test). 任何 fail 不可接受.

#### 7.2 Single-symbol baseline backtest

```bash
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000
python backtest.py --csv data/vxx_4h.csv --interval 4h --capital 10000
```

期望:
- UVXY: total ret +82.81% (±0.01pp)
- VXX: total ret +226.79% (±0.01pp)

注: TURBO=ON/OFF 已不存在 (tactical archived + grid_bot.py 撤销到 8c41c62), 不需要测 TURBO=ON 路径.

#### 7.3 Multi-symbol backtest

```bash
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv --allocations 0.5 0.5 \
    --capital 10000 --interval 4h --label phase6_verify
```

期望: total_ret_pct = +153.93% (Phase 3 已 verify, 这里二次确认 Phase 4 修 bug 没破坏).

#### 7.4 State recovery 测试

CLAUDE.md §3 章定义状态恢复链路:
- PnL FIFO ← SQLite pnl_fifo_queue
- StateMachine ← SQLite state_machine_state
- GridEngine ← JSON {DB_FILE}.grid.json
- 底仓股数 ← text {DB_FILE}.base_shares.txt
- RiskManager ← SQLite risk_state

**测试做法**:
1. 跑一次 backtest 到 OFFENSIVE_GRID 状态 (在 STATE 切换时 ctrl-C)
2. 重启同 backtest, 验证 GridBot.recover() 从 SQLite + JSON 恢复状态 (含 base_shares, grid center, FIFO 队列)
3. 多标的: 每个 sub-bot 独立 db_path + 独立恢复

如果测试发现状态不能恢复 (例如 D 路径期间 state JSON schema 变了): 修 + log.

#### 7.5 main.py paper-mode 启动测试

**前置**: Phase 5 main.py 已改写为 `MultiSymbolOrchestrator` 入口, 加 `--paper-verify` flag.

```bash
IBKR_HOST=127.0.0.1 IBKR_PORT=4004 IBKR_CLIENT_ID=1 python main.py --paper-verify
```

(`--paper-verify` 是 Phase 5 加的 dry-run flag, 连 IBKR 但不实际下单, 验证装配)

期望:
- 连接 IBKR paper account
- 拉 NetLiquidation 注入 config.TOTAL_CAPITAL
- 装配 2 个 sub-bot (UVXY + VXX), 各 50% allocation
- 验证两个 client_id 独立连接 (用 ClientIdAllocator)
- 跑一个 step (no orders), 然后优雅 shutdown

如果无法 paper test (e.g., IBKR Gateway 没起): 报告 skipped + 标记 follow-up. 不强制.

### Phase 8 — Docs + Registry (~30 min)

#### 8.1 README.md (Phase 2 已撤销, 重新写 §5 + §3 + §4)

更新 §5 能力快照:
- 默认 baseline: **UVXY+VXX 50/50, 5y +153.93%** (年化 +20.78%, MDD 15.09%)
- 单标的参考: UVXY +82.81%, VXX +226.79%
- 删除 V49 +109.91% 数据 (历史参考留 git log, README 不再 mention)
- 删除战术化 +73.81% / +165.41% 数据 (archived)

更新 §3 加多标的部署示例:
- main.py 默认 MultiSymbolOrchestrator (Phase 7.X)
- run_multi_backtest.py 回测命令

更新 §4 一眼看懂运行方式: 多标的命令为主, 单标的为 legacy.

#### 8.2 CHANGELOG.md (新建)

按 Keep-a-changelog 格式:

```markdown
# Changelog

## [2026-05-15] Production Refactor

### Removed (Archived to archive/tactical/)
- 战术化模块: tactical_config / tactical_rules / session_manager
- Sweep 工具: scripts/prove_tactical / scripts/_proof_runner / scripts/tune_tactical
- 战术化测试: tests/proof/
- 战术化 reports: tactical_proof_of_impossibility / tactical_extended_screening

### Added
- 多标的并行回测: scripts/run_multi_backtest.py (D 路径 D4)
- Walk-forward 验证: scripts/walk_forward_fixed.py (D 路径 D1)
- VXX 5y 历史数据: data/vxx_4h.csv (D 路径 P4)
- main.py MultiSymbolOrchestrator 入口 (本次 Phase 7)
- Symbol 级隔离: bot_factory / orchestrator / capital_allocator / client_id_allocator / account_risk

### Fixed
- risk_manager multi-symbol capital reference bug (Phase 4.A, re-apply D5 76ebb4d)
- entry_filter getattr fallback 与 config default 不一致 (Phase 4.B)
- config.py L209 stale "默认改 -1000" 注释 (Phase 4.C)

### Investigated (未修复, follow-up)
- V49 → 8c41c62 -27pp 回归 root cause (Phase 4.D, 见 reports/v49_27pp_regression.md)
```

#### 8.3 PROJECT_STATUS.md

hook 自动维护. Phase 8 完成后手动 verify 内容反映重构后 state (last_commit 等).

#### 8.4 CLAUDE.md

**不动** (user 明确要求).

#### 8.5 archive/tactical/README.md (新建)

解释 archive 目的:
- 战术化经 251 trial 严证伪通过, 默认 OFF, 不在主路径
- 完整证据见 reports/ 内 archive 的两个 report
- 不应在 main 路径恢复; 若要"复活", 从 archive/ checkout 回 main 路径 + 重新走 spec/plan/verify 流程

#### 8.6 reports/refactor_2026_05_15.md (新建)

本次重构完整报告. 含:
- 7 phase 实际执行步骤 + 时间
- 每 phase 的 git commit SHA
- 4 bug fix root cause + minimal fix + verification
- Phase 6 测试输出 (实际 shell 输出, 不是 LLM 编造)
- Phase 4.D 链接 reports/v49_27pp_regression.md
- 剩余风险 + follow-up

#### 8.7 reports/v49_27pp_regression.md (新建)

Phase 4.D git-bisect 输出. 含:
- bisect 步骤详细 (每步 commit + backtest ret + good/bad 判定)
- 找到的 first bad commit
- diff 摘要
- 推荐处理 (回滚 / accept / follow-up)

---

## 4. Deliverables 总览

**新建**:
- `archive/tactical/` 目录及内部所有 (tactical 模块 + sweep 工具 + tests + reports + runtime data)
- `archive/tactical/USAGE_BEFORE_ISOLATION.md` (Phase 1.1 grep 结果)
- `archive/tactical/README.md` (archive 说明 + 复活流程)
- `archive/tactical/CLEANUP_LOG.md` (Phase 5 grep cleanup 日志)
- `CHANGELOG.md` (Phase 7.2)
- `reports/refactor_2026_05_15.md` (Phase 7.6, 重构完整报告)
- `reports/v49_27pp_regression.md` (Phase 7.7, git-bisect 结果)

**修改**:
- `state_machine.py` (保留 D 路径 6 状态扩展)
- `entry_filter.py` (保留 + Phase 4.B getattr fix)
- `test.py` (保留 + Phase 1.3 skipTest for tactical test)
- `config.py` (Phase 2 撤销到 8c41c62, 然后 Phase 4 re-apply BACKTEST_DEFAULT_CAPITAL=10000 + ENTRY_MAX_WAIT_BARS env default=12 + Phase 4.C L209 stale comment 修)
- `risk_manager.py` (Phase 2 撤销, 然后 Phase 4.A re-apply D5 multi-symbol fix)
- `main.py` (Phase 7 改写为 MultiSymbolOrchestrator 入口, 加 `--paper-verify` flag)
- `README.md` (Phase 7.1, 撤销 + 重写 §5/§3/§4)

**撤销到 8c41c62 (然后部分 re-apply)**:
- `backtest.py`, `grid_bot.py`, `ibkr_executor.py`, `report_generator.py`, `requirements.txt`, `simulated_executor.py`, `trade_logger.py`

**保留不动**:
- `bot_factory.py`, `orchestrator.py`, `capital_allocator.py`, `client_id_allocator.py`, `account_risk.py` (untracked, 多标的基础设施)
- `scripts/run_multi_backtest.py`, `scripts/walk_forward_fixed.py`, `scripts/run_baseline_grid.py`, `scripts/screen_symbols.py`, `scripts/manual_resume.py` (untracked + D 路径产物)
- `data/vxx.py`, `data/vxx_4h.csv`, `data/riot_4h.csv`, `data/soxl_4h.csv`, `data/soxs_4h.csv`, `data/mara_4h.csv`, `data/multi_pull.py` (untracked + D 路径)
- `docs/superpowers/specs/`, `docs/superpowers/plans/` (本次 + 之前所有 spec/plan)
- `findings.md`, `progress.md`, `task_plan.md` (调查文档)
- `CLAUDE.md`, `PROJECT_STATUS.md` (user 要求不动)

---

## 5. 工作量 + 时间表

| Phase | 时间 |
|---|---|
| Phase 1: Scan + isolate tactical | 30 min |
| Phase 2: Surgical revert | 20 min |
| Phase 3: Verify multi-symbol | 10 min |
| Phase 4.A: risk_manager fix | 20 min |
| Phase 4.B: entry_filter fix | 15 min |
| Phase 4.C: config stale comment | 5 min |
| Phase 4.D: git-bisect -27pp | 2-3 h |
| Phase 5: main.py MultiSymbolOrchestrator | 45 min |
| Phase 6: Grep cleanup | 20 min |
| Phase 7: Tests + recovery + paper | 30 min |
| Phase 8: Docs + registry | 30 min |
| **Total** | **~6-7 h** (主要瓶颈 Phase 4.D bisect 跑回测时间) |

---

## 6. 风险 + 缓解

| 风险 | 缓解 |
|---|---|
| Phase 2 撤销时 broke multi-symbol 依赖 | Phase 3 立即 verify +153.93%; 不通过则 diagnostic |
| Phase 4.D bisect 期间 working tree dirty 干扰 | `git stash --include-untracked` 全部 stash, bisect 完 pop. 中断时 `git bisect reset` |
| Phase 6.5 paper mode 启动失败 (IBKR connection 没起) | 不强制. 报告 skipped + 标 follow-up |
| Phase 6.4 state recovery 测试发现 schema 漂移 | 加 migration logic; 修 + log; 不允许 LLM 跳过 |
| Phase 7.2 CHANGELOG 误描述删除/保留功能 | 用 reviewer subagent 比对 git diff 与 CHANGELOG 一致性 |
| 战术化 16 个 test skip 后, 未来想"复活"战术化 | archive/tactical/README 写明复活流程 |
| 撤销 main.py 后 Phase 7 重写时与 8c41c62 main.py 单标实例化模式不兼容 | Phase 7 main.py 改写时 reference bot_factory.build_multi_symbol_bots 现有签名, 不自创新 API |
| Phase 4.A re-apply risk_manager fix 与 8c41c62 risk_manager 基线有 conflict | D5 fix 是加 helper + 改 check_hard_stop / check_position_limit, 不动其他 6 层风控. 在 8c41c62 base 上重新写一次 (不依赖 D5 commit 的 diff) |
| 已经 commit 在 main 上的 D 路径 commit (P1-D6, 28+ 个) 被 reset 影响 | **不 reset main 分支历史**. Phase 2 只 `git checkout <SHA> -- <file>`, 不动 commit graph. D 路径 commits 仍在 git log 可查 |
| 14 个 candidate commit (V49→8c41c62) 中含 "Delete CLAUDE.md / Delete data/qqq.py / Delete PROJECT_STATUS.md" 这些不影响回测但 bisect 时 git checkout 会丢文件 | bisect 时只跑 backtest, 不关心其他文件. bisect 结束后 stash pop 恢复 |
| LLM 写 "已验证 / 已通过" 但没真跑命令 (user 明确禁止) | 每个 verification step 必须 paste 实际 shell 输出到 commit message / report. 用 reviewer subagent 比对 |

---

## 7. 不在范围 (诚实声明)

- **不动 CLAUDE.md** (用户明确要求, 仍然适用)
- **不实际跑 paper account ≥ 4 周对账** (CLAUDE.md §7 user manual step)
- **不修复 Phase 4.D 找到的 -27pp root cause** (只报告, 修复是独立 spec)
- **不重写 backtest.py / grid_bot.py 核心逻辑** (回到 `8c41c62` 状态)
- **不增加新交易功能** (例如 trend-following 替代战术化)
- **不重新跑 V49 worktree** (已知数据)
- **不删除 archive/tactical/** (即使用户后来不要这些, archive 保留作历史)
- **不动 untracked 文件中已经被 D 路径 commit 引用的部分** (例如 docs/superpowers/plans/ 中的 plan 文件)
- **不 force-push / 不 rewrite git history** (CLAUDE.md §7 死规矩, 仍然适用)

---

## 8. 验收清单 (最终 deliverable 给 user 的输出)

按 user 原话:
> 改动文件、回退内容、漏洞根因、测试命令与真实结果、是否复现 `+153.93%` 回报、剩余风险

最终输出文档结构 (在 `reports/refactor_2026_05_15.md` 中):
1. **改动文件**: 完整 git diff stats + 文件分类 (新建 / 修改 / 撤销 / 保留)
2. **回退内容**: Phase 2 surgical checkout 的 11 个文件的 8c41c62 状态摘要
3. **漏洞根因**: 4 bug 各自 root cause + minimal fix + verification (Phase 4)
4. **测试命令与真实结果**: Phase 6 所有 6 个测试的实际 shell 输出 (复制粘贴, 不允许 LLM 重写)
5. **是否复现 +153.93% 回报**: Phase 3 + Phase 6.3 的两次独立运行实际输出
6. **剩余风险**: 已知 follow-up + Phase 4.D 找到的 -27pp 但未修 + Phase 6.5 paper test 可能 skip 等
