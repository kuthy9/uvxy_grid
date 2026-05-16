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
- 多标的并行回测: `scripts/run_multi_backtest.py` (UVXY+VXX 50/50, +201.69% 5y)
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
