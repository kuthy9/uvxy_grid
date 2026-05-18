# Grid ETF — 高波动 ETF 网格策略研究 + 实盘执行

> 一个面向**高波动 / 弱趋势 / 高流动性 ETF** 的网格交易研究与执行仓库。
> 同一份业务代码同时驱动回测和实盘 (IBKR)。
> 目标不是把某个标的的曲线调到最好看，而是建立**可复现**的候选发现 + 验证 + 上线流程。
>
> 项目动态进度去 [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) 看。
> 协作规则去 [`CLAUDE.md`](./CLAUDE.md) 看。

---

## 1. 项目目标

1. **筛 ETF**：用量化指标 (波动 / 趋势 / 流动性 / 成本) 找到适合网格的 ETF。
   不依赖直觉，不依赖个例。
2. **稳网格**：在选定的标的 + 周期上训练出经过 walk-forward / OOS / 稳定性扫描验证的参数。
3. **能上线**：研究阶段的回测和真实账户的实盘走**同一套业务代码**，避免"回测好看 / 实盘失灵"。
4. **守纪律**：一切阈值变化都要拿证据说话；一切实盘行为都要对得上回测假设。

不在范围内的事：高频 / 做市、多策略组合、杠杆 ETF 之外的衍生品、
为单一行情手写一次性脚本。

---

## 2. 适合的市场结构

策略数学上依赖标的具备：

- **高波动**：足够日内 / 日间幅度让网格穿行。
- **弱趋势 / 均值回归**：长期不会单边远离中轴。
- **高流动性**：spread 小、深度足、成本可控。
- **可承受成本**：手续费 + 滑点 + 监管费占毛利的比例必须低到留得住盈余。

不适合：单边趋势 ETF (例如 QQQ 长期向上)、低波动 ETF、低成交额 ETF。
当前默认基线是 **UVXY 4h**，因为 UVXY (1.5× VIX 短期期货 ETF) 自然具备高波动 + 无长期趋势。

---

## 3. 系统能做什么

### 3.1 共享业务核心 (回测 / 实盘同源)

```
┌─────────────────────────────────────────────────────────────┐
│                    grid_bot.GridBot                          │
│  状态机 + 入场筛选 + 动态网格 + 6 层风控 + 周报 + 紧急清算   │
│  + 启动状态恢复 + 与 IBKR 对账                                │
└──────┬──────────────┬──────────────┬─────────────────────────┘
       │              │              │
   ┌───▼────┐    ┌───▼────┐    ┌───▼────────┐
   │ Clock  │    │Executor│    │RiskManager │
   │ Live / │    │ IBKR / │    │ (clock 注入)│
   │ Hist   │    │ Sim    │    │             │
   └────────┘    └────────┘    └────────────┘
       ▲              ▲
   ┌───┴──────┐  ┌────┴──────┐
   │ main.py  │  │backtest.py│
   │  实盘     │  │   回测     │
   └──────────┘  └───────────┘
```

业务逻辑只写一份：修 bug 一次性同时生效于回测和实盘。

### 3.2 状态机 + 启动恢复

四状态 FSM (`SCANNING → WAITING_ENTRY → ACTIVE_GRID → EXIT_PENDING`) + SQLite / JSON 持久化。

启动恢复链路：

```
PnL FIFO       ← SQLite pnl_fifo_queue
StateMachine   ← SQLite state_machine_state
GridEngine     ← JSON   {DB_FILE}.grid.json
底仓股数        ← text   {DB_FILE}.base_shares.txt
RiskManager    ← SQLite risk_state (或 IBKR broker_prev_close fallback)
IBKR 对账      ← reqAllOpenOrders + portfolio() (实盘)
```

实盘重启会自动接管自己挂出的 GTC、撤孤儿单、检测本地 ↔ broker 漂移并落事件。

### 3.3 真实化回测

回测撮合不是"理想化按 limit 100% 成交"。`SimulatedExecutor` 内置：

- **市价单半价差滑点** (`BT_MARKET_SLIP_BPS`，默认 5bps)
- **限价单触价 / 穿越概率** (`BT_LIMIT_FILL_PROB_TOUCH/_CROSS/_GAP_FILL_PROB`)
- **SEC Section 31 + FINRA TAF 监管费** (与实盘同口径)
- **种子化随机** (`BT_RANDOM_SEED`)，结果可复现
- 一键退化到旧理想化撮合做 A/B 对比 (`BT_REALISTIC_FILLS=False`)

### 3.4 调参与验证 (`scripts/tune.py`)

- 多进程并行 grid search (默认 `--workers 7`)
- Top-K **walk-forward** (滚动 train+valid 窗口)
- **OOS 三分法** 互迁移 + decay 分析
- **±20% 稳定性扫描** (找出对参数最敏感的维度)
- 输出全量证据到 `runtime/experiments/<interval>/`，决策落到 `*_summary.md`

跨 interval 一键汇总: `scripts/compare_intervals.py`。

### 3.5 风控

`RiskManager` 6 层 (硬止损 / 当日 PnL / 单仓占比 / 闪崩前收保护 / 交易时段 / 财报冻结)，
全部走注入的 `clock`，回测 / 实盘行为一致。

### 3.6 报表与日志

- 每周一 16:30 ET 生成 HTML 周报 (`runtime/reports/weekly_YYYYWww.html`)
- 所有交易、状态转换、风险事件入 SQLite (`runtime/trades.db`)
- 累计 realized PnL 以 IBKR `reqPnL` 推送为权威，不被本地误算覆盖

### 3.7 部署

- 本地：`python main.py` 直连 TWS / IB Gateway
- Docker：`docker compose up -d` 起 `gnzsnz/ib-gateway` + 本仓 bot；
  `depends_on: condition: service_healthy` 等 Gateway 登录完成再起 bot
- IBKR 端口白名单 (`config.KNOWN_IBKR_PORTS`) 防止 paper / live 误判

---

## 4. 一眼看懂运行方式

```bash
# 测试
python test.py

# 回测 (默认 4h, 单标的)
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 2000

# 多标的回测 (UVXY+VXX 50/50)
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv --allocations 0.5 0.5 \
    --capital 10000 --interval 4h

# 调参 (单 interval 完整 pipeline)
python scripts/tune.py --interval 4h --workers 7

# 跨 interval 汇总
python scripts/compare_intervals.py

# 实盘 Paper 验证 (smoke test)
python main.py --paper-verify

# 实盘 (默认单 UVXY, Paper)
IBKR_HOST=127.0.0.1 IBKR_PORT=4002 python main.py

# 实盘 (显式双标的 UVXY+VXX 50/50, Paper)
IBKR_HOST=127.0.0.1 IBKR_PORT=4002 python main.py --symbols UVXY VXX

# 实盘 (Docker, 需要本地 .env 提供 TWS_USERID/TWS_PASSWORD/TRADING_MODE 等)
docker compose up -d
```

更详细的运行细节看 [`CLAUDE.md` §5](./CLAUDE.md)。

---

## 5. 当前能力快照

> 数值会随研究推进变化, 权威来源是 [`findings.md`](./findings.md) +
> `runtime/experiments/` 下的产物.

- **当前默认基线**: `UVXY` × `4h` × `MultiSymbolOrchestrator` (2026-05-17 起单标的, VXX 可显式 `--symbols UVXY VXX` 启用)
- **回测样本**:
  - UVXY 5y 4h CSV (Alpaca IEX, 2021-01-04 → 2026-04-24)
  - VXX  ~5y 4h CSV (Alpaca IEX, 2021-05-17 → 2026-05-14)
- **支持的策略周期**: `15m` / `1h` / `4h` / `1d` (改 `config.STRATEGY_INTERVAL` 即可)
- **当前 5y 回测指标 ($10,000 capital, 真实化撮合)**:
  - **多标的合并 (UVXY+VXX 50/50)**: ret **+153.93%** / annu +20.78% / MDD ~15.09%
  - UVXY sub-bot ($5,000): ret +70.38% / final $8,519.16
  - VXX  sub-bot ($5,000): ret +237.48% / final $16,873.86
- **单标的参考** (回测 + sanity, 不是实盘默认):
  - UVXY: +82.81% / 年化 +12.05% / MDD 15.86%
  - VXX: +226.79% / 年化 +26.78% / MDD 15.17%
- **战术化已 archived** (2026-05-15 严证伪通过): 详见
  [`archive/tactical/reports/tactical_proof_of_impossibility.md`](./archive/tactical/reports/tactical_proof_of_impossibility.md)
- **测试覆盖**: 252 个 `test_*` 用例 (含 ~51 个 tactical-related skipped)
- **历史 baseline**: D5 spec baseline +153.93% (matches D5 report exactly; grid_capital 2x oversubscription bug fixed in commit 0b1abcf → bit-identical reproducibility)

---

## 6. 依赖

```bash
pip install -r requirements.txt
```

```
ib_insync>=0.9.86
yfinance>=0.2.30
pandas>=2.0.0
pyarrow>=14.0.0
numpy>=1.24.0
requests>=2.31.0      # 仅 data/qqq.py (Alpaca 拉数) 需要
```

要求 Python ≥ 3.10 (需要 `zoneinfo` + PEP 604 union types)。

---

## 7. 风险与限制

这是研究与执行框架，不是面向终端用户的生产级交易系统。

- **历史不代表未来**：回测样本 5 年内表现良好，不等于下 5 年同样如此。
- **bar 级建模上限**：真实化撮合已经覆盖 half-spread / 触价概率 / 监管费，
  但仍是 bar 级，不复现 tick / 队列细节。
- **小资金成本敏感**：$2000 量级下 IBKR 手续费占毛利 80%+；放大资金前需要
  Paper 账户实跑复核。
- **网格不打趋势**：标的真的进入单边趋势行情时网格会跑输持有；本仓库不解决这件事，
  应该靠选标的来回避。
- **实盘必须逐步上线**：建议先 Paper 至少 4 周，对账成交、滑点、PnL 与回测假设的差距。

---

## 8. 文档分工

- [`README.md`](./README.md) — 项目目标 + 能力介绍 (本文)
- [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) — 项目动态进度 (随开发更新)
- [`CLAUDE.md`](./CLAUDE.md) — 协作规则 / 架构约束 / 死规矩
- [`docs/resilience.md`](./docs/resilience.md) — 断电恢复审计：启动→接管时序、Synology 手动 checklist、缺口清单（核心修复 deferred per P1）
- [`docs/uvxy_param_audit.md`](./docs/uvxy_param_audit.md) — UVXY 参数校准审计（2026-05-17 切换单 UVXY 默认时落账，结论：核心参数已 UVXY-tuned，资金分配比例本轮不需重新校准）
- [`docs/telegram_sidecar.md`](./docs/telegram_sidecar.md) — Telegram 只读 sidecar 部署/运维手册：token rotation、10 命令、5 push 通道、troubleshooting
- [`docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md`](./docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md) — 审计 + sidecar 设计规范（P1/P2/P3 三条硬约束 + §4 审计 + §5 sidecar）
- [`docs/superpowers/plans/2026-05-17-resilience-audit.md`](./docs/superpowers/plans/2026-05-17-resilience-audit.md) — 审计的 task-by-task 执行计划
- [`docs/superpowers/plans/2026-05-17-telegram-sidecar.md`](./docs/superpowers/plans/2026-05-17-telegram-sidecar.md) — sidecar 的 task-by-task 执行计划
- 代码注释 + `runtime/experiments/<interval>/*` — 历史决策的原始证据
