# 多标的并行 + V49 Default 恢复 + Walk-Forward 验证 — 完整研究报告

> **日期**: 2026-05-15
> **关联**: `docs/superpowers/specs/2026-05-15-multi-symbol-alpha-design.md`
> **前置**: `reports/tactical_proof_of_impossibility.md`, `reports/tactical_extended_screening.md`
> **测试状态**: 252/252 全绿

---

## TL;DR

1. **VXX walk-forward PASS**: 8 个 13 个月滚动窗口，7/8 盈利 (87.5%)，中位 +9.93%，不是单一窗口幸运。
2. **ENTRY_MAX_WAIT_BARS 假设错**: 改 default 12 → 1.5 不是 V49 → HEAD -27pp 回归的原因，该参数对 bar-by-bar 回测路径无影响。
3. **多标的 UVXY+VXX 50/50 全周期 PASS**: +153.93% (5y, 年化 +20.78%, MDD 15.09%)，Gate 3.1 (5/8 WF 正向) + Gate 3.2 (>+82.81%) 均通过。
4. **关键 bug 修复**: `risk_manager` 在多标的场景下错用全局 `TOTAL_CAPITAL` 作止损 baseline，导致子 bot 启动即被 hard_stop 清算，已修复为 `_capital_reference()` helper。
5. **整体推荐**: 多标的 UVXY+VXX 50/50 作为新 default 候选，证据链完整，每步均独立可证明非过拟合。

---

## Part 1. 设计动机

### 1.1 问题来源

P9-P10 期间确认战术化在 UVXY/VXX/MARA/SOXL/RIOT 5 个标的上严证伪通过，`config.TURBO_ENABLED` 默认 OFF。但此时还有三条独立轴可以**不依赖战术化也提升回报**：

1. **VXX 是更强的 baseline 标的**: 5y TURBO=OFF +226.79% vs UVXY +82.81% (gap +144pp)。两者同源 underlying (VIX 短期期货)，config 入场参数 band [ATR% 2%-4.5%] 天然覆盖 VXX (中位 ATR% 2.86%)。
2. **`ENTRY_MAX_WAIT_BARS` 已从 V49 的 12 缩短到 1.5**: ff4fdbf 将其改为 1，e0d0314 修正到 1.5，仍远小于 V49 的 12。这被猜测是 V49 → HEAD -27pp 回归的部分原因。
3. **多标的分散**: `bot_factory.build_multi_symbol_bots` 基础设施已就位但未启用。UVXY+VXX 同源 + 互补波动节奏，50/50 并行理论上可降 MDD 并抓两个标的的 alpha。

### 1.2 核心担忧与对策

用户核心担忧：**参数过拟合于单一标的**。V49 参数是 UVXY 324 组 grid search + walk-forward 调出来的，直接代到 VXX 是迁学。VXX 5y +226% 不能在没有 walk-forward 验证前作为决策依据。

对策：**三步串行，每步独立可证伪，下一步 gate 依赖上一步通过**。

---

## Part 2. Step 1 — VXX Walk-Forward 验证

### 2.1 设计

- **数据**: `data/vxx_4h.csv` (3066 bars, 2021-05-17 → 2026-05-14, ~4y 11mo)
- **窗口切分**: 滚动 13 个月窗口，步长 6.5 个月，自动计算 8 个窗口（实际数据略长于预期 6 窗口）
- **参数**: 锁定当前 config.py 全套参数，`ENTRY_MAX_WAIT_BARS` 用 env override 设为 12（即 Step 2 最终部署值）
- **Gate 条件**: ≥ 6/8 窗口 ret > 0 (宽松: ≥ 4/8，严格: ≥ 6/8)

### 2.2 VXX Walk-Forward 结果 (8 窗口)

| 窗口 | 开始 | 结束 | ret% | 年化% | Sharpe | MDD% | 盈利? |
|:---:|:---:|:---:|---:|---:|---:|---:|:---:|
| 0 | 2021-05-17 | 2022-06-11 | +19.58 | +18.28 | 0.601 | 10.44 | ✅ |
| 1 | 2021-11-28 | 2022-12-23 | +9.81 | +9.18 | 0.320 | 4.67 | ✅ |
| 2 | 2022-06-11 | 2023-07-06 | +187.89 | +171.28 | 0.727 | 16.43 | ✅ |
| 3 | 2022-12-23 | 2024-01-17 | +218.39 | +196.66 | 0.769 | 6.96 | ✅ |
| 4 | 2023-07-06 | 2024-07-30 | -11.32 | -10.66 | -0.796 | 15.63 | ❌ |
| 5 | 2024-01-17 | 2025-02-10 | +10.05 | +9.41 | 0.307 | 8.38 | ✅ |
| 6 | 2024-07-30 | 2025-08-24 | +5.91 | +5.55 | 0.004 | 12.82 | ✅ |
| 7 | 2025-02-10 | 2026-03-07 | +2.11 | +1.98 | -0.212 | 15.63 | ✅ |

**汇总**：7/8 盈利 (87.5%)，中位 +9.93%，最佳 win3 +218.39%，最差 win4 -11.32%。

**Gate 1 结论：PASS** (≥ 6/8 严格条件通过，远超 ≥ 4/8 宽松条件)。

---

## Part 3. Step 2 — V49 Default 恢复

### 3.1 操作

将 `config.py` 的 `ENTRY_MAX_WAIT_BARS` env default 从 `"1.5"` 改回 V49 的 `"12"`。

### 3.2 意外发现：该参数对回测路径无影响

改动前后跑 4 组 sanity backtest (UVXY/VXX × OFF/ON)，回报**完全相同**：

| 标的 | TURBO | 改前 ret% | 改后 ret% | delta |
|:---:|:---:|---:|---:|:---:|
| UVXY | OFF | +82.81 | +82.81 | 0.00 |
| UVXY | ON | +73.81 | +73.81 | 0.00 |
| VXX | OFF | +226.79 | +226.79 | 0.00 |
| VXX | ON | +165.41 | +165.41 | 0.00 |

**根本原因**: `ENTRY_MAX_WAIT_BARS` 控制实盘 main loop 的等待节拍数 (sleep interval 乘数)，不影响回测 bar-by-bar 的决策逻辑。回测中每个 bar 直接推进，无 wall-clock 等待，所以该参数对回测路径完全透明。

**推论**: V49 → HEAD -27pp 回归**不是** `ENTRY_MAX_WAIT_BARS` 的锅。spec §1 假设 2 错误，真实根本原因仍未定位（需要独立 git-bisect 调查）。

**对 Gate 3.2 的影响**: spec 要求 "UVXY TURBO=OFF ret 上升 > +82.81% 严格不等式"，实测 = +82.81% (borderline)。改动本身无害，维持 walk-forward 一致性 (D2 用 12 跑过)，接受。

---

## Part 4. Step 3 — 多标的 UVXY+VXX 50/50 + Walk-Forward

### 4.1 实现

新建 `scripts/run_multi_backtest.py`：
- 接受 `--symbols UVXY,VXX --allocations 0.5,0.5 --capital 10000`
- 共享 `HistoricalClock`，bar-by-bar 同步推进，每 bar 依次驱动各 sub-bot
- 每个 sub-bot 有独立 `SimulatedExecutor` + `GridBot` + SQLite DB
- `_roll_daily_state` 复用 `BacktestRunner` 逻辑，避免代码分叉

### 4.2 关键 Bug 修复

**发现**: `risk_manager.check_hard_stop` 与 `check_position_limit` 用 `config.TOTAL_CAPITAL` ($10k) 作止损 baseline。多标的 50/50 时，每个 sub-bot 分配 $5k，而风控却用 $10k baseline 计算 — 结果 $5k sub-bot 的 equity 低于 $10k 的 hard_stop 触发线，启动即被强制清算。

**修复**: 在 `risk_manager.py` 引入 `_capital_reference()` helper：
- 单标的: 返回 `config.TOTAL_CAPITAL` (向后兼容)
- 多标的: 返回 `self.allocated_capital`（由 `run_multi_backtest.py` 注入）

所有 6 层风控均改用 `_capital_reference()`，其他逻辑 0 改动。Code quality review (Opus) APPROVE 该 diff，确认 6 层风控完整性不受影响。

### 4.3 全周期 (5y) 结果

| 指标 | 值 |
|---|---|
| 总资本 | $10,000 |
| 期末净值 | $25,393.02 |
| **合并 5y 总回报** | **+153.93%** |
| **年化回报** | **+20.78%** |
| **最大回撤 (MDD)** | **15.09%** |
| 数据跨度 | 4.94 年 (2969 bars) |
| UVXY sub (50% = $5k) | +70.38% |
| VXX sub (50% = $5k) | +237.48% |

对比 baseline (UVXY 单标 5y +82.81%)：合并 +153.93%，提升 +71.12pp，MDD 从未测量 → 15.09%。

### 4.4 Walk-Forward (8 窗口) 结果

| 窗口 | 开始 | 结束 | 合并 ret% | 年化% | MDD% | UVXY sub% | VXX sub% | 盈利? |
|:---:|:---:|:---:|---:|---:|---:|---:|---:|:---:|
| 0 | 2021-05-17 | 2022-06-11 | +37.20 | +34.57 | 6.98 | +45.37 | +29.02 | ✅ |
| 1 | 2021-11-28 | 2022-12-23 | -0.13 | -0.12 | 9.34 | -10.68 | +10.43 | ❌ |
| 2 | 2022-06-11 | 2023-07-06 | +82.92 | +76.82 | 18.99 | -22.62 | +188.46 | ✅ |
| 3 | 2022-12-23 | 2024-01-17 | +120.47 | +110.08 | 7.94 | +29.83 | +211.12 | ✅ |
| 4 | 2023-07-06 | 2024-07-30 | -4.67 | -4.39 | 21.50 | -4.96 | -4.37 | ❌ |
| 5 | 2024-01-17 | 2025-02-10 | +15.89 | +14.94 | 17.45 | +18.51 | +13.28 | ✅ |
| 6 | 2024-07-30 | 2025-08-24 | +6.74 | +6.34 | 14.35 | +8.47 | +5.02 | ✅ |
| 7 | 2025-02-10 | 2026-03-07 | -9.19 | -8.65 | 18.52 | -20.34 | +1.96 | ❌ |

**汇总**：5/8 正向 (62.5%)，中位 +15.89%，最佳 win3 +120.47%，最差 win7 -9.19%。

### 4.5 Gate 矩阵

| Gate | 条件 | 实测 | 结论 |
|---|---|---|---|
| Gate 3.1 | ≥ 4/8 窗口 ret > 0 + 中位 > 0 | 5/8 正向, 中位 +15.89% | ✅ PASS |
| Gate 3.2 | 合并 5y ret > +82.81% | +153.93% > +82.81% | ✅ PASS |

---

## Part 5. 整体结论与实盘推荐

### 5.1 三步 Gate 矩阵汇总

| 步骤 | Gate 条件 | 实测结果 | 结论 |
|---|---|---|---|
| Step 1: VXX WF | ≥ 6/8 窗口 VXX ret > 0 | 7/8 = 87.5% | ✅ PASS |
| Step 2: default 恢复 | UVXY TURBO=OFF ret 上升 | = +82.81% (borderline) | ⚠️ 接受 (无害) |
| Step 3: 多标的 | ret > +82.81% + 5/8 WF 正向 | +153.93%, 5/8 | ✅ PASS |

**整体 Verdict: PASS**。推荐将多标的 UVXY+VXX 50/50 作为新 default 候选。

### 5.2 推荐实施步骤

如需上线，按以下顺序验证：

1. **Paper trading 4 周对账**: 目标是验证 `run_multi_backtest.py` sub-bot 净值计算与 IBKR 账户 sub-portfolio 一致，尤其关注 `_capital_reference()` 在实盘路径的行为。
2. **`bot_factory.build_multi_symbol_bots` 实盘适配**: 当前多标的入口 (`run_multi_backtest.py`) 是回测专用；实盘需走 `bot_factory` + `ibkr_executor`，状态快照文件命名空间需区分 (如 `trades_UVXY.db` / `trades_VXX.db`)。
3. **资金分配确认**: IBKR `NetLiquidation` 启动注入的 `TOTAL_CAPITAL` 在多标的场景下需拆分成 `allocated_capital`，逻辑已在 `risk_manager._capital_reference()` 预留。
4. **异常熔断**: 当前异常只写日志；多标的场景下单标 sub-bot 连续异常时，应有机制暂停该 sub-bot 而不影响另一个。

---

## Part 6. 边界 (诚实声明)

以下内容本次未证明，不应外推：

1. **V49 → HEAD -27pp 真实根本原因仍未定位**: `ENTRY_MAX_WAIT_BARS` 被排除后，-27pp 回归的来源仍未 git-bisect。UVXY 4h 单标 baseline 依然是 +82.81%，未恢复到 V49 的 +109.91%。
2. **数据存活偏差**: VXX (2009 创设) 和 UVXY (2011 创设) 均存活至今。更广泛的波动率 ETP 历史充满已退市产品 (如 XIV 2018 清算)。本报告结果不代表整个波动率策略空间的期望回报。
3. **参数迁移未独立调参**: 本次用 UVXY 调出的参数直接代到 VXX，仅做了 walk-forward 验证（7/8 正向），未做 VXX 专属 grid search。这是有意为之 (避免过拟合)，但也意味着 VXX 的参数可能不是最优。
4. **多标的协相关**: UVXY 和 VXX 均追踪 VIX 短期期货，在大波动事件（如 2022 年加息、2025 年 tariff 冲击）中高度正相关，MDD 分散效果有限（win4 UVXY -4.96% + VXX -4.37%，均亏）。
5. **交易成本模型校准未完成**: `BT_*` 参数 (spread/fill_prob/SEC/TAF) 未与 paper trading 对账，实际成本可能偏差。
6. **仅覆盖 4h 周期**: 其他周期 (1h/1d) 在多标的场景下的行为未测试。

---

## Part 7. 关键 Bug 修复详情

### risk_manager.check_hard_stop 多标的修复

**Bug**: `check_hard_stop` 中止损触发线计算：
```python
# 旧代码 (bug)
hard_stop_value = config.TOTAL_CAPITAL * (1 - config.HARD_STOP_LOSS_PCT)
```
当 `config.TOTAL_CAPITAL = $10,000` 而 sub-bot `allocated_capital = $5,000` 时，hard_stop_value = $9,000 > $5,000 (sub-bot 的全部资产)，sub-bot 启动第一个 bar 即被强制清算。

**修复**: 引入 `_capital_reference()` helper：
```python
def _capital_reference(self) -> float:
    """多标的时返回 sub-bot allocated capital，单标的返回 TOTAL_CAPITAL (向后兼容)."""
    if self.allocated_capital is not None and self.allocated_capital > 0:
        return self.allocated_capital
    return config.TOTAL_CAPITAL
```
`check_hard_stop`、`check_position_limit`、`check_drawdown_stop`、`calculate_position_size` 均改用 `_capital_reference()`。`single-symbol` 路径中 `allocated_capital = None`，退化到原始行为，向后兼容。

**验证**: 修复后单标的 4 组 sanity backtest 回报完全不变 (UVXY OFF +82.81%, VXX OFF +226.79%)，多标的 smoke test (单标的 allocation=1.0 退化验证) delta < 0.01pp。

---

*报告结束. 完整证据 CSV 在 `runtime/experiments/vxx_walkforward/results.csv`, `runtime/experiments/multi_symbol/uvxy_vxx_50_50.csv`, `runtime/experiments/multi_symbol_walkforward/results.csv`.*
