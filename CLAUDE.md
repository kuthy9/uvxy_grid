# CLAUDE.md — Grid ETF 项目规则

本文件是这个仓库的项目级 instruction，**只描述规则**，不记录变更日志。
项目动态进度去 `PROJECT_STATUS.md` 看，项目目标和能力介绍去 `README.md` 看。
任何 AI 助手在动代码前先读完本文。

---

## 1. 这个项目是什么

一个面向 ETF 网格策略的量化研究 + 实盘执行仓库。

- 目标市场结构：高波动 / 弱趋势 / 高流动性 / 成本可承受。
- 当前默认工作配置：`UVXY + 4h + 当前 config.py 中的参数版本`。
  这是**当前基线**，不是不可质疑的真理；任何参数 / 标的 / 执行逻辑变化都必须拿证据说话。
- 同一份业务代码 (`grid_bot.GridBot`) 同时驱动回测与实盘，禁止再分叉。

不属于本项目的事：SaaS 化、微服务化、为单一标的写一次性脚本、不带证据的换标的或调参。

---

## 2. 目录结构

```text
grid/
├── 入口
│   ├── main.py                实盘入口 — 组装 LiveClock + IBKRExecutor + GridBot
│   ├── backtest.py            回测入口 — 组装 HistoricalClock + SimulatedExecutor + GridBot
│   └── test.py                单元 / 场景测试集 (unittest)
│
├── 核心业务逻辑 (实盘 / 回测共享，改了直接影响策略行为)
│   ├── grid_bot.py            主循环 / 状态恢复 / reconcile / 状态持久化
│   ├── state_machine.py       4 状态 FSM (SCANNING / WAITING / ACTIVE / EXIT) + SQLite 持久化
│   ├── entry_filter.py        入场筛选 (ADX / ATR% / BB / 财报 / 流动性)
│   ├── grid_engine.py         动态网格引擎 (EMA 中轴 + ATR 间距)
│   ├── risk_manager.py        6 层风控 (clock 注入)
│   └── pnl_tracker.py         FIFO 配对 + 胜率统计 + 紧急清算
│
├── 基础设施 (可迁移到下一个量化项目)
│   ├── interfaces.py          Clock / Executor 协议
│   ├── ibkr_executor.py       IBKR 实盘执行器 + reconcile_on_startup
│   ├── simulated_executor.py  回测模拟撮合 (half-spread + 触价概率 + SEC/TAF)
│   ├── data_provider.py       Yahoo + parquet 缓存
│   ├── indicators.py          EMA / ATR / ADX / BB / RSI
│   ├── trade_logger.py        SQLite 日志
│   └── report_generator.py    HTML 周报
│
├── 配置
│   └── config.py              唯一配置入口 (端口白名单 / interval / 当前参数版本 / BT_*)
│
├── scripts/                   研究 / 调参工具
│   ├── tune.py                并行 grid search + walk-forward + OOS + 稳定性
│   └── compare_intervals.py   跨 interval 汇总
│
├── data/                      历史行情 CSV (UVXY 5y × 15m/1h/4h/1d 等)
│
├── runtime/                   运行时产物 (不进版本控制)
│   ├── experiments/           调参证据 (按 interval 分子目录 + 总结)
│   ├── reports/               HTML 周报输出
│   ├── data_cache/            parquet 缓存
│   ├── trades.db              实盘 SQLite (启动时自建)
│   └── trades.db.grid.json / .base_shares.txt   状态快照 (恢复用)
│
├── Dockerfile / docker-compose.yml / .dockerignore
├── requirements.txt
├── README.md                  项目介绍 (目标 + 能力展示)
├── CLAUDE.md                  本文 — 项目规则
└── PROJECT_STATUS.md          项目动态进度 (会随开发更新)
```

---

## 3. 哪些文件不能乱改

下面这些文件改之前必须先想清楚影响范围；不要顺手"重构"。

- `interfaces.py` — Clock / Executor 协议契约。改这里就是改全系统的耦合面；
  `ibkr_executor.py` 与 `simulated_executor.py` 必须同步保证行为对齐。
- `grid_bot.py` — 实盘 / 回测共用的策略大脑。任何改动必须保持两边行为一致，
  禁止只为某一边打补丁。
- `risk_manager.py` — 6 层风控的最终防线。禁止绕过、禁止重复实现、禁止丢 clock 注入。
- `ibkr_executor.py` — 与 IBKR 通信的唯一出入口。除此之外任何模块都不能 `import ib_insync`。
- `simulated_executor.py` — 回测撮合模型。修改成本 / 滑点 / 概率参数前必须能解释为什么。
- `state_machine.py` 与状态快照文件 (`*.db.grid.json` / `*.base_shares.txt` / SQLite tables)
  — 实盘恢复链路依赖它们；删旧字段 / 改 schema 必须有迁移方案。
- `config.py` — 唯一配置真源。新增配置就在这里加，不要散落到模块里。
- `test.py` — 把不变量钉死的地方；改测试前先反问"是测试错了，还是真的改变了行为预期"。

`PROJECT_STATUS.md` 与 `README.md` 文档可以改，但要符合各自定位（见文件顶部说明）。

---

## 4. 怎么运行测试

```bash
python test.py
```

要求：

- 任何与策略 / 风控 / 状态机相关的改动都必须跑过完整 `test.py`，全绿才算完成。
- 修业务代码后**新增对应测试**；只改测试让旧测试通过是 anti-pattern。
- 涉及启动取价 / `_prev_close` / delayed 降级时，至少跑下面这几个：
  `TestIBKRExecutorPrevCloseLogic`、`TestIBKRExecutorDelayedDegrade`、
  `TestRiskInitializeFromDbPriority`。

---

## 5. 怎么运行 pipeline

### 5.1 回测

```bash
# 默认 4h
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 2000

# 切到其他周期
python backtest.py --csv data/uvxy_15min.csv --interval 15m --capital 2000
python backtest.py --csv data/uvxy_1h.csv   --interval 1h  --capital 2000
python backtest.py --csv data/uvxy_1d.csv   --interval 1d  --capital 2000
```

注意：`--csv` 给定时 `--days` 被忽略 — 读整份 CSV。

### 5.2 调参 / 研究

```bash
# 单一 interval 的完整 grid search + walk-forward + OOS + ±20% 稳定性
python scripts/tune.py --interval 4h --workers 7
# --quick 跑精简网格做快速冒烟

# 跨 interval 汇总
python scripts/compare_intervals.py
```

调参产物落到 `runtime/experiments/<interval>/`，新版本的总结写一份 `*_summary.md`
放在 `runtime/experiments/`。**不要把调参产物 commit 进 git。**

### 5.3 实盘

```bash
# 方式 A: 本地 TWS / Gateway
IBKR_HOST=127.0.0.1 IBKR_PORT=7497 IBKR_CLIENT_ID=1 python main.py

# 方式 B: Docker (推荐, gnzsnz/ib-gateway)
#  - 准备 .env 提供 TWS_USERID / TWS_PASSWORD / TRADING_MODE 等 (不要 commit)
#  - paper: IBKR_PORT=4004 (默认)；live: 改 4003
docker compose up -d
```

启动时强制走 `config.KNOWN_IBKR_PORTS` 端口白名单：未知端口直接 `sys.exit(2)`，
防止 paper / live 误判。`TOTAL_CAPITAL` 必须在启动时由 IBKR `NetLiquidation` 注入，
读不到就 `sys.exit(3)`，绝不带任意资金假设进场。

---

## 6. 修改代码后必须做什么

按顺序执行：

1. **跑 `python test.py`** — 必须全绿。
2. **行为变化要补 / 改测试** — 新行为没测试 = 默认会被未来的人破坏。
3. **回测 / 实盘一致性自检**：
   - 改了任何业务模块？grep 一遍这条新代码，确认没人直接调 `datetime.now()` 或
     `import ib_insync`。
   - 改了 `simulated_executor` 或 `ibkr_executor`？想清楚两边行为是否仍然对齐。
4. **如果改的是策略阈值**：
   - 必须给出回测对比 (相对当前 baseline)。
   - 必须经过 walk-forward 或 OOS 验证，不能只看单窗口收益。
   - 在 `runtime/experiments/<interval>/` 下落盘证据；总结写到 `*_summary.md`。
5. **更新 `PROJECT_STATUS.md`** — 项目状态变了就更新；这是动态进度文件。
6. **不要更新 `CLAUDE.md`** — 除非真的是项目规则变了 (流程 / 禁令 / 入口命令等)。
   做了什么改动这种事属于 `PROJECT_STATUS.md`，不是这里。
7. **`README.md`** 只在项目目标或能力展示发生变化时更新。
8. 涉及外部 API / 标准 / 库行为时，引用具体来源 (URL / 文档 / 官方说明)。

---

## 7. 哪些命令危险

下列命令在没有用户明确授权前**不要执行**：

- `git push` / `git push --force` / `git reset --hard` / `git clean -f`
  — 会动远端或销毁本地工作。
- `git commit --no-verify` / 任何跳过 hook 或签名的命令。
- `rm -rf runtime/` / `rm runtime/trades.db*` / 任何删 SQLite / 删状态快照的操作
  — 会丢实盘历史和恢复链路。
- `docker compose down -v` / 删 `tws_settings/` 卷
  — 会丢 IBKR Gateway 登录 settings，触发 2FA 重新登录。
- 给 `main.py` 设 `IBKR_PORT=7496 / 4001 / 4003` 中任一项
  — 这是 **LIVE** 端口，非"演练"。配 live 之前先确认账户、资金、风控参数。
- 直接修改 `runtime/trades.db` 的 SQLite 行 / 删除已成交记录
  — 会污染胜率、PnL、状态恢复。
- 在没有证据的前提下改 `config.py` 的 `BT_*` 真实化参数让回测变好看
  — 这叫造数据，不叫优化。
- 回测脚本里直接 `datetime.now()` / 业务模块里 `import ib_insync`
  — 不算"危险"但是死规矩，破了就是回测/实盘分叉。

---

## 8. 当前开发优先级

下面这个清单只列**方向**；具体进度去 `PROJECT_STATUS.md` 看。

1. **更适合网格的 ETF 候选筛选流程** — 目标是建立可复现的"高波动 + 弱趋势 + 高流动性"
   候选发现机制，而不是人工拍脑袋换标的。波动 / ADX / 成交额 / 成本敏感性都要量化。
2. **回测到实盘的一致性复核** — 滑点 / 触价概率 / 监管费已经进了回测，
   接下来需要 Paper 实跑至少 4 周做对账，必要时校准 `BT_*` 参数。
3. **外部告警与连续异常熔断** — 当前异常只写日志；目标是关键事件有外发通道，
   连续 N 次异常应自动切到 EXIT_PENDING 或暂停。
4. **成本 / 滑点敏感性扩展** — `scripts/tune.py` 的稳定性扫描是起点，
   需要扩到不同 spread / 不同 fill 概率假设下的退化曲线。
5. **多标的并行运行的可行性研究** — 当前 `GridBot` 单标的；如果要支持多标的，
   先想清楚资金分配、风控隔离、状态文件命名空间怎么设计。

不在当前优先级里的事 (但任何人发现就指出)：

- 回测 / 实盘行为不一致
- 未来函数 / 数据泄漏
- 交易时段假设错误
- 文档与代码漂移
- 参数被改了但没有任何基线对比

---

## 9. 死规矩 (违反必须立即改回)

- 业务模块 (`grid_bot` / `risk_manager` / ...) 不读 wall-clock 时间，全部走 `self.clock.now()`。
- `ib_insync` 只在 `ibkr_executor.py` 中出现。
- 不跳过交易时段去发市价单（紧急清算的硬止损路径除外，且必须显式标注）。
- 累计 realized PnL 以 `IBKRExecutor.get_realized_pnl()` 为权威；不要把 `accountValues.RealizedPnL`
  当累计 PnL（那是当日值）。
- 数量规整一律走 `config.round_quantity()`。
- 同一业务概念不在多个文件硬编码常量，必须放 `config.py`。
- 不要造 `*_v2.py` / `*_new.py` 影子文件；直接修原文件。
- 回测 / 实盘共用 `GridBot`，禁止复制一份"回测专用业务逻辑"。
- 不在没有证据的情况下宣称"这个参数更优"。
- 不用 `--no-verify` / `--no-gpg-sign` / `git reset --hard` 这类捷径来绕过问题。
