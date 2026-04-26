# PROJECT_STATUS.md — Grid ETF 项目动态进度

> 这是动态文档。每次有方向 / 参数 / 代码上的实质变化，**改这里**，不要去改 `CLAUDE.md`。
> 项目规则去 `CLAUDE.md` 看，目标和能力介绍去 `README.md` 看。

最后更新: **2026-04-25**

---

## 1. 当前基线 (一眼版)

| 维度 | 值 | 出处 |
|---|---|---|
| 标的 | `UVXY` | `config.SYMBOL` |
| 周期 | `4h` | `config.STRATEGY_INTERVAL` |
| 参数版本 | **V49** | `config.py` 头部注释 |
| 回测样本 | UVXY 4h × 5 年 (2021-01 ~ 2025-12) | `data/uvxy_4h.csv` |
| 真实化撮合 | 启用 (half-spread + 触价概率 + SEC/TAF) | `config.BT_REALISTIC_FILLS = True` |
| 总资金 (回测) | `--capital` 注入 (默认 2000) | `config.BACKTEST_DEFAULT_CAPITAL` |
| 总资金 (实盘) | 启动时 IBKR `NetLiquidation × (1 - reserve)` | `main.py` |

V49 关键阈值 (相对默认 QQQ-tuned 的偏差见 `config.py` 行内注释):

| 参数 | V49 当前值 |
|---|---|
| `ENTRY_MAX_ADX` | 20.0 |
| `ENTRY_MAX_ATR_PCT` | 0.045 |
| `GRID_SPACING_ATR_MULTIPLIER` | 0.5 |
| `GRID_RECENTER_THRESHOLD_ATR` | 1.0 |
| `EXIT_MAX_ADX` | 22.0 |

---

## 2. V49 回测表现 (4h × 5y)

来源: `runtime/experiments/4h/FINAL.json` + `runtime/experiments/V49_summary.md`

| 指标 | V47 baseline | V48 | **V49 (current)** |
|---|---|---|---|
| 5y 总收益 | +84.61% | +101.87% | **+103.78%** |
| 年化 | +13.08% | +15.12% | **+15.34%** |
| Sharpe | +0.371 | +0.519 | **+0.545** |
| 最大回撤 | 20.65% | 10.36% | **8.88%** |
| 胜率 | 79.5% | 79.5% | **83.8%** |
| Round trips | 39 | 44 | 37 |
| 总手续费 | $73.86 | $79.88 | $70.37 |

验证证据:

- 4h 全量网格 324 组 grid search → `runtime/experiments/4h/search_log.csv`
- Top-8 walk-forward × 6 窗口 → `runtime/experiments/4h/walkforward.csv`
  - V49 平均 valid Sharpe = +1.03, V48 = +1.00
- 三分法 OOS 迁移 → `runtime/experiments/4h/oos_report.csv`
- ±20% 稳定性扫描 → `runtime/experiments/4h/stability.csv`
- 跨 interval 决策 → `runtime/experiments/V49_summary.md`

---

## 3. 跨 interval 调研 (同方法论)

| Interval | 5y 收益 | Sharpe | MDD | Trips | WF valid Sh | 结论 |
|---|---|---|---|---|---|---|
| 15m | -21.83% | -1.03 | 28.18% | 19 | -1.17 | ✗ 负收益 |
| 1h | -20.32% | -0.45 | 55.82% | 91 | -1.38 | ✗ 灾难性 |
| **4h** | **+103.78%** | **+0.545** | **8.88%** | **37** | **+1.03** | ✓ 综合最优 |
| 1d | +500.39% | +0.46 | 16.48% | 2 | +0.25 | ⚠ 样本太小 |

结论：4h 是 UVXY 网格当前最优时间级别。15m / 1h 在 UVXY 上持续负收益 (噪声 + hard stop +
手续费占比高)；1d round trips 仅 2 次，walk-forward valid Sharpe 0.25，稳健性不足。

---

## 4. 代码体量快照

| 模块 | 行数 |
|---|---|
| `grid_bot.py` | 1131 |
| `test.py` | 1110 |
| `backtest.py` | 621 |
| `ibkr_executor.py` | 490 |
| `scripts/tune.py` | 494 |
| `grid_engine.py` | 415 |
| `simulated_executor.py` | 371 |
| `report_generator.py` | 356 |
| `entry_filter.py` | 353 |
| `pnl_tracker.py` | 337 |
| `risk_manager.py` | 318 |
| `state_machine.py` | 292 |
| `config.py` | 283 |
| `data_provider.py` | 276 |
| `trade_logger.py` | 214 |
| `interfaces.py` | 159 |
| `indicators.py` | 154 |
| `main.py` | 118 |
| `scripts/compare_intervals.py` | 101 |
| **合计 (含 test.py)** | **~7593** |

测试: `test.py` 包含 **15 个 TestCase 类 / 77 个 `test_*` 方法**。

---

## 5. 部署状态

- **本地开发**: macOS (darwin 24.6.0)，工作目录 `/Users/krisjiang/Desktop/grid`，
  当前**不是**一个 git 仓库 (待 `git init`)。
- **Docker**: `Dockerfile` (python:3.12-slim + tzdata + America/New_York) +
  `docker-compose.yml` (gnzsnz/ib-gateway + uvxy-grid，container 间走 `ib-gateway:4004`)。
- **IBKR 端口白名单**: TWS 7497/7496 + IB GW 原生 4002/4001 + gnzsnz socat 4004/4003
  (`config.KNOWN_IBKR_PORTS`)。未列入 → `main.py` 直接 `sys.exit(2)`。

---

## 6. 已完成的关键改动 (高层概要)

只列**仍在影响当前架构**的事项；具体 bug 编号和补丁细节去看代码注释。

- 回测与实盘共用 `GridBot`，删除回测专属业务逻辑分叉。
- `Clock` / `Executor` 抽象注入；`RiskManager` 也走 `clock.now()`。
- 实盘启动状态恢复链路：PnL → StateMachine → GridEngine → 底仓 → RiskManager →
  IBKR `reconcile_on_startup` (接管自家 GTC、撤孤儿单、检测漂移)。
- 回测真实化：half-spread (`BT_MARKET_SLIP_BPS`) + 触价概率
  (`BT_LIMIT_FILL_PROB_TOUCH` / `_CROSS` / `_GAP_FILL_PROB`) + SEC + FINRA TAF + 种子化。
- `TOTAL_CAPITAL` 无硬编码 fallback：实盘从 IBKR 拉，回测 `--capital`，测试 `setUpModule`
  注入。读不到直接 `sys.exit(3)`。
- 启动取价防御深度: `STARTUP_PRICE_TIMEOUT_SEC` 默认 15s + `MARKET_DATA_TYPE` 自动 live → delayed
  降级 + `Executor.get_prev_close()` 用 `reqHistoricalData(5D, 1day)` 过滤未完成的当日 bar +
  `RiskManager.initialize_from_db` 多优先级 (`risk_state(<24h) > broker_prev_close >
  daily_snapshot > current_price`)。
- 周报频率从每日改为每周一 16:30 ET。

---

## 7. 当前开发优先级 (与 CLAUDE.md §8 同步)

按优先级递减:

1. **候选 ETF 可复现筛选流程**
   - 目标: 把"换标的"从拍脑袋变成可量化流程，输出候选清单 + 筛选证据。
   - 状态: 未启动。需要先确定波动 / 趋势 / 流动性 / 成本敏感性指标的口径。

2. **回测 → 实盘一致性 Paper 复核**
   - 目标: 用 Paper 账户实跑至少 4 周，对账成交价、滑点、监管费与回测的差异，
     必要时校准 `config.BT_*`。
   - 状态: 未启动 (基础设施已经就位)。

3. **外部告警 + 连续异常熔断**
   - 目标: 关键事件 (硬止损、reconcile 漂移、连续异常) 有外发通道；连续 N 次异常自动暂停。
   - 状态: 未启动。当前异常只落 `runtime/grid_trader.log`。

4. **成本 / 滑点敏感性扩展**
   - 目标: 在 `scripts/tune.py` 的 ±20% 扰动基础上，加上不同 spread / fill 概率假设下的退化曲线。
   - 状态: 起点已存在 (`stability.csv`)，但维度还不够。

5. **多标的并行可行性研究**
   - 目标: 在不破坏单标的稳健性的前提下，先纸面设计资金分配 / 风控隔离 /
     状态命名空间方案。
   - 状态: 未启动；只是占位提醒，不要在没设计前直接改 `GridBot`。

---

## 8. 已知风险 / 仍未关闭的问题

- `$2000` 这个量级在 IBKR 下手续费占毛利 80%+；策略数学上能赚，但实际净利薄。
  扩资金前需要先证明真实滑点假设。
- 回测仍是 bar 级建模，无法复现 tick / 队列层面的撮合细节。Paper 复核之前
  V49 的真实表现不可直接外推。
- 没有外部告警通道；现在出问题只能靠日志事后诊断。
- 当前不是 git 仓库；版本历史目前依赖文件 mtime 和实验产物落盘。建议尽早 `git init`
  并加上 `.gitignore` (见 §10)。

---

## 9. 数据 / 产物盘点

- 行情 CSV (`data/`):
  - `uvxy_15min.csv` ~35k bars
  - `uvxy_1h.csv` ~9.6k bars
  - `uvxy_4h.csv` ~3.1k bars
  - `uvxy_1d.csv` ~1.3k bars
  - `qqq.py` 是 Alpaca 拉数脚本 (需要 API key)
- 实验产物 (`runtime/experiments/`):
  - `15m/` `1h/` `4h/` `1d/` 各自 `{search_log, best_by_metric, walkforward, oos_report,
    stability, FINAL}.csv|json`
  - `V49_summary.md` — 当前决策依据
- 实盘产物 (运行时生成，**不要 commit**):
  - `runtime/trades.db` SQLite
  - `runtime/trades.db.grid.json` 网格快照
  - `runtime/trades.db.base_shares.txt` 底仓快照
  - `runtime/grid_trader.log`
  - `runtime/reports/weekly_*.html`
  - `runtime/data_cache/*.parquet`

---

## 10. 追踪文件 (.gitignore / .env) 状态

- `.gitignore`: **目前不存在** (项目还不是 git 仓库)。`git init` 之前必须先建一个，
  最低限度需要忽略：`runtime/`、`__pycache__/`、`*.pyc`、`.DS_Store`、`.env`、
  `tws_settings/`、`data_cache/`、本地 IDE 配置 (`.vscode/`、`.idea/`)。
- `.env`: **目前不存在**。`docker-compose.yml` 引用了 `${TWS_USERID}` /
  `${TWS_PASSWORD}` / `${TRADING_MODE}` 等变量；docker 部署前需要建一个本地 `.env`
  且永远不进 git。建议同时维护一份 `.env.example` 列出所需变量名 (无值)。
- `.dockerignore`: 已存在并覆盖了 `runtime/` / `__pycache__/` / `.git/` / `.claude/` 等。

---

## 11. 下一次更新建议

发生下面任一项就更新这个文件：

- `config.py` 中的参数版本变化 (V49 → V50 ...)。
- 标的或周期切换。
- 新增或淘汰 `runtime/experiments/<interval>/` 子目录。
- 优先级 §7 中任意一项状态改变 (未启动 → 进行中 → 完成)。
- §8 中已知问题被关闭，或新发现需要登记。
- 仓库本身的工程态变化 (例如 git 初始化、CI 接入、`.env.example` 提交)。
