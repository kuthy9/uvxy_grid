# Changelog

All notable changes to this project will be documented in this file.

## [2026-05-17] Resilience Audit — Deferred Fixes B1/B2/B3 (core code, user-approved P1 lift)

User explicitly lifted P1 for these three fixes after reviewing the audit's
deferred items. Investigation confirmed data/*.py scripts are research-only
(no live trading code reads data/) → B4 (data scripts) NOT fixed; documented
as operational hygiene only.

### Fixed
- **B1 (`main.py`)** — restart-loop log flood. `_exit_with_backoff(code)`
  helper sleeps `BOOT_RETRY_BACKOFF_SEC` (default 30s, env-overridable)
  before `sys.exit` on the 4 connect-related exit paths. `sys.exit(2)` for
  config errors left untouched (hard-exit so operator notices immediately).
  - Commit: 29cc38b (5 tests, full pytest 36 passed).
- **B2 (`grid_bot.py`)** — IBKR Gateway boot-window race in
  `_reconcile_with_broker`. New `_fetch_reconcile_with_retry(max_attempts=3,
  backoff_sec=(2,5,10))` retries when local state is in a position-holding
  state (`is_position_holding_state`) AND broker reports empty. Other cases
  accepted immediately. Prevents the worst-case: bot restart inside the
  Gateway hydration window → drift #2 → wipe local FIFO → REAL MONEY risk.
  - Commit: 33cc056 (8 tests, full pytest 44 passed).
- **B3 (`ibkr_executor.py`)** — application-layer disconnect awareness.
  `connect()` subscribes `disconnectedEvent` once (idempotent across
  reconnects). `_on_disconnected` handler logs + clears
  `_market_data_type_effective` so next reconnect re-evaluates live/delayed
  cleanly. Does NOT trigger reconnect from the event handler (would block
  ib_insync's asyncio loop). `disconnect()` marks `_intentional_disconnect=True`
  BEFORE `self.ib.disconnect()` to win the race with the event. New
  `ensure_connected()` helper as a reconnect-if-needed seam for callers.
  - Commit: 68978a1 (12 tests, full pytest 56 passed).

### Verified
- 56/56 project pytest passes (31 pre-existing + 5 B1 + 8 B2 + 12 B3).
- All three audit checks (C, G, H) now report OK on `audit_resilience.py`.
- `docs/resilience.md §3` gap log updated to reflect fixed state.

### Not fixed (intentional, per investigation 2026-05-17)
- **B4 (data/*.py scripts not cron-managed)** — kept as WARN. Data scripts
  are research-only (verified: zero core trading file imports/reads from
  `data/`). Operational hygiene only, not a trading-correctness risk.

## [2026-05-17] Telegram Read-Only Sidecar (read-only, P1-clean)

### Added
- `telegram_bot/` 新 Python 包（30+ 文件，~2000 行）：
  - `bot.py` — long-poll + Dispatcher + 后台 push watcher 线程
  - `config.py` — env-driven，缺关键 env 即 fail-fast（无静默 fallback）
  - `auth.py` — 单 chat_id allowlist + 速率限制的未授权告警日志
  - `tg_client.py` — 裸 requests 调 Telegram Bot API + 429 退避梯度 [5/30/120/300]s
  - `smoke.py` — `python -m telegram_bot.bot --smoke` 离线一轮验证
  - `readers/sqlite_ro.py` — 强制 `?mode=ro&immutable=0` 连接
  - `readers/grid_json.py` — `{db}.grid.json` 解析 + 半写竞态 100ms retry-once
  - `readers/base_shares.py` — `{db}.base_shares.txt` 解析
  - `readers/log_tail.py` — inode+offset 跟踪、rotate-safe 日志 tail
  - `readers/ibkr_ro.py` — ib_insync 只读包装 + **import-time write-API ban**（任何 placeOrder/cancelOrder 方法名会让模块拒绝加载）
  - `handlers/*` — 10 个只读命令：/help /status /positions /pnl /grid /orders /risk /report /logs /health
  - `push/*` — 4 个 push 通道：state_watcher / risk_watcher / log_watcher / heartbeat（覆盖 5 类事件：状态机转换、风控触发、错误/IBKR 断线/bot 启停、心跳停滞）
- `telegram_bot/tests/` — 71 个单元测试（含 conftest 共享 fixtures、handlers、watchers、auth、tg_client、bot Runtime、smoke）
- `docker-compose.yml` — 追加 `telegram-bot` 服务（独立 sidecar、与主 bot 同镜像不同 CMD、`IBKR_CLIENT_ID=99` 错开主 bot=1、`/app:ro` 挂载防御）
- `.env.example` — 追加 Telegram + 调度 env 变量模板
- `docs/telegram_sidecar.md` — 运维 runbook（**§2 token rotation 是部署前第一步**、§3 部署步骤、§4 命令表、§5 push 说明、§6 troubleshooting）
- `docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md` §5 — sidecar 设计规范
- `docs/superpowers/plans/2026-05-17-telegram-sidecar.md` — task-by-task 执行计划

### Hard constraints honored (binding for this round)
- **P1 — 零核心代码改动**：未修改 17 个核心交易文件中的任何一个。sidecar 完全外部观察（read SQLite `?mode=ro` / read JSON 文件 / tail log / IBKR read-only API）。
- **P2 — 零硬编码 / 零伪代码**：所有凭证走 env，缺失即 fail-fast；token 不进仓库、不写日志（`test_url_never_logged` 测试断言）；smoke 是"已运行"的证据来源。
- **P3 — TDD + 回归**：每个模块（reader/handler/watcher）都走 TDD；71/71 sidecar 测试 + 31/31 pre-existing 项目测试，零回归。

### Security
- `ibkr_ro.py` import-time assertion 检查公开方法名是否匹配 `placeOrder|cancelOrder|modifyOrder|reqGlobalCancel`，命中则 `RuntimeError` 拒绝加载（纵深防御）
- chat_id allowlist + 未授权请求静默丢弃 + 速率限制告警
- `/logs` 输出对 IBKR 账户 ID 正则脱敏（`U\d{7}` 和 `DU\d{7}` → `***`）
- docker-compose 挂载 `/app:ro` 让"不写共享卷"约束在容器层硬性强制

### Verified
- 102/102 项目级 pytest（71 sidecar + 31 pre-existing），零回归
- smoke：`TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python -m telegram_bot.bot --smoke` exit=0
- import-time ban：`python -c "from telegram_bot.readers import ibkr_ro; print('ok')"` → `ok`

### Deployment runbook
部署前必须先去 BotFather rotate token（之前的 token 在 brainstorming 阶段被泄漏到对话历史）。完整步骤见 `docs/telegram_sidecar.md §2`。

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
