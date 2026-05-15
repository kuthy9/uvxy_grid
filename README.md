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

六状态 FSM (`SCANNING → WAITING_ENTRY → OFFENSIVE_GRID → DEFENSIVE_GRID → EXIT_PENDING → COOLDOWN`)
+ SQLite / JSON 持久化。旧 `ACTIVE_GRID` 在 DB 恢复时被翻译为 `OFFENSIVE_GRID`。

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

# 回测 (默认 4h)
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 2000

# 调参 (单 interval 完整 pipeline)
python scripts/tune.py --interval 4h --workers 7

# 跨 interval 汇总
python scripts/compare_intervals.py

# 实盘 (本地)
IBKR_HOST=127.0.0.1 IBKR_PORT=7497 python main.py

# 实盘 (Docker, 需要本地 .env 提供 TWS_USERID/TWS_PASSWORD/TRADING_MODE 等)
docker compose up -d
```

更详细的运行细节看 [`CLAUDE.md` §5](./CLAUDE.md)。

---

## 5. 当前能力快照

> 数值会随研究推进变化；权威来源是 [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) 与
> `runtime/experiments/` 下的产物。

- **当前默认基线**：`UVXY` × `4h` × `TURBO=OFF` (战术化默认 OFF, 见下方说明)
- **回测样本**：UVXY 5 年 4h CSV ($10k capital, 真实化撮合); VXX ~5 年 4h CSV (2021-05-17 → 2026-05-14)
- **支持的策略周期**：`15m` / `1h` / `4h` / `1d` (改 `config.STRATEGY_INTERVAL` 即可，
  系统其他模块全部跟随)
- **当前 5y 回测指标 (4h, $10000 capital, 真实化撮合)**:
  - **UVXY TURBO=OFF**: ret **+82.81%** / annu +12.05% / Sharpe +0.32 / MDD 15.86%
  - **UVXY TURBO=ON** (战术化默认参数): ret +73.81% (战术 4 个 action 默认 0 触发, 实质走相似 legacy 路径)
  - **VXX TURBO=OFF**: ret **+226.79%** / annu +26.78% / Sharpe +0.32 / MDD 15.17%
  - **VXX TURBO=ON**: ret +165.41% (同上, 战术 4 个 action 默认 0 触发)
- **战术化不可落地性证明** (2026-05-15): **严证伪通过**. UVXY + VXX × 4h × ≤5y × $10k cap × **251 个 sweep trial 中 0 个满足 B 三条** (Defensive/Forced/Profit-protect 触发 > 0 + 平均 session 寿命 ≤ 20 bars + 5y 回报 ≥ TURBO=OFF baseline). 战术化"短线收割"在合规边界内不能落地. `config.TURBO_ENABLED` 默认 OFF. 完整证据: [`reports/tactical_proof_of_impossibility.md`](./reports/tactical_proof_of_impossibility.md).
- **历史参考**: V49 worktree (`4fdb801` initial commit, $2k cap) 上的 5y +109.91% / Sharpe +0.54 / MDD 8.88% 是仓库历史 baseline, 与当前 HEAD 之间存在 -29pp legacy 回归 (与战术化命题正交, 未 git-bisect 定位).
- **测试覆盖**：252 个 `test_*` 用例 (含 4 个 `TestTacticalActionsReachable` 中性 regression 锁战术化 4-action 代码可达性 + 12 个 P9 fixup 显式 TURBO=ON patch)

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
- 代码注释 + `runtime/experiments/<interval>/*` — 历史决策的原始证据
