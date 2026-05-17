# Changelog

All notable changes to this project will be documented in this file.

## [2026-05-17] Resilience Audit (read-only, P1-clean)

### Added
- `scripts/audit_resilience.py` — 11-check 只读审计脚本（A 主机 / B compose / C-I 静态代码检查 / D-E SQLite+JSON 完整性 / J 磁盘 / K 心跳）。退出码 0/1/2 = OK/WARN/FAIL。可由 Synology Task Scheduler 每日运行。
- `tests/audit/test_audit_resilience.py` — 28 个单元测试，每个检查走 TDD（失败用例 + 通过用例）。
- `docs/resilience.md` — 启动→接管时序图、Synology Web UI 手动 checklist、首次审计输出的缺口清单（8 条 WARN/FAIL）、再跑指引、可选 cron 配置。
- `docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md` §4 — 审计设计规范。
- `docs/superpowers/plans/2026-05-17-resilience-audit.md` — task-by-task 执行计划。
- `.gitignore` — 添加 audit 输出目录说明。

### Hard constraints honored (binding for this round)
- **P1 — 零核心代码改动**：未修改 `main.py`、`grid_bot.py`、`orchestrator.py`、`ibkr_executor.py`、`risk_manager.py`、`grid_engine.py`、`config.py` 等 17 个核心文件中的任何一个。审计发现需要核心改动的缺口（C/G/H/I 的 4 项 WARN）全部 DEFERRED，记入 `docs/resilience.md` 等待单独批准的下一轮。
- **P2 — 零硬编码 / 零伪代码**：所有路径来自 `os.environ.get` + 默认值或 argparse；缺关键值即 fail-fast 而非静默 fallback。
- **P3 — TDD + 回归**：每个检查 (A–K) 都走"先写失败测试 → 实现 → 再跑同测试 → 跑邻近回归"的 4 步流程。

### Verified
- 31/31 项目级 pytest 通过（28 audit + 3 pre-existing），零回归。
- `python scripts/audit_resilience.py --repo-root .` 跑通；本地 dev 分支 exit=2（D FAIL 因为没 trades.db，K WARN 级联；C/G/H/I WARN 是 DEFERRED 项；A WARN 需手动 --ack-host-checked）。

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
- `archive/tactical/README.md` + `CLEANUP_LOG.md` + `USAGE_BEFORE_ISOLATION.md`
- `CHANGELOG.md` (本文件)
- `reports/refactor_2026_05_15.md` (重构完整报告)
- `reports/v49_27pp_regression.md` (Phase 4.D git-bisect)
- `tests/main_assembly/` (main.py 装配 smoke test)

### Fixed
- `risk_manager._capital_reference()` helper: multi-symbol sub-bot 不再误用全账户
  TOTAL_CAPITAL 作 baseline (D5 发现的 bug, re-applied 在 8c41c62 base 上)
- `risk_manager.check_daily_loss` 多标的模式下委托给 account_risk (T6 codereview gap)
- `grid_bot._capital()` helper + `symbol` property: sizing 用本 bot allocated_capital
- `grid_engine.py` 3 处 wall-clock 违规 (CLAUDE.md §9): 强制 explicit current_time
- `entry_filter.py` L193: 删 dead `getattr` fallback `-1.0`
- `config.py`: 加 `ENTRY_MAX_ADX_SLOPE = -0.5` (撤销到 8c41c62 后丢失), 删 stale "默认改 -1000" 注释
- `state_machine.py`: 补 commit T2 6-state FSM (b388f7b 漏 stage)
- `grid_bot.py:738` DynamicGridEngine() 未传 grid_capital, multi-symbol sub-bot 2x
  oversubscribe (因 fallback 用全账户 TOTAL_CAPITAL × GRID_CAPITAL_RATIO 而非
  本 bot allocated × ratio). Live + backtest 实测影响. 修后 multi 从 +201.69%
  (inflated) 恢复为 +153.93% (正确, 与 D5 baseline bit-identical). (commit 0b1abcf)
- `ibkr_executor.py.__init__`: 加 4 个 kwargs (symbol/exchange/currency/client_id),
  bot_factory 装配 live bot 时不再 TypeError.

### Investigated (Not Fixed, Follow-up)
- V49 (`4fdb801`) → `8c41c62` 之间 -27pp 回归 root cause (first bad = `e369447`,
  WAITING_ENTRY 节拍与 STRATEGY_INTERVAL 解耦). 详见 `reports/v49_27pp_regression.md`.

### Surgical Revert (Phase 2)
撤销到 `8c41c62` (然后部分 re-apply fix):
- grid_bot.py, risk_manager.py, config.py, main.py, README.md (撤销后又部分重写)
- backtest.py, ibkr_executor.py, report_generator.py, requirements.txt,
  simulated_executor.py, trade_logger.py (这些在 HEAD 已经等于 8c41c62)

保留 D 路径增强 (未撤销):
- state_machine.py (6-state FSM)
- entry_filter.py (T1 ADX_slope + S3 recent_range)
- test.py (252 tests 含 tactical guards)
- bot_factory.py (T2 + Task 2 修)
