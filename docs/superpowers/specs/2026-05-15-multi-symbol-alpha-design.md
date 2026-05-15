# 多标的并行 + V49 default 恢复 + walk-forward 验证 — Design Spec

> **日期**: 2026-05-15
> **状态**: design (待用户 review)
> **关联**: 战术化默认 OFF (P9), `reports/tactical_proof_of_impossibility.md`, `reports/tactical_extended_screening.md`

---

## 1. 背景

P9-P10 期间确认战术化在 UVXY/VXX/MARA/SOXL/RIOT 上严证伪通过, `config.TURBO_ENABLED` 默认 OFF. 但还有三个独立轴可以**不靠战术化也提升回报**:

1. **VXX 是更强的 baseline 标的**: 5y TURBO=OFF +226.79% vs UVXY +82.81% (gap +144pp), 同源 underlying (VIX 短期期货), config 入场参数 band [ATR% 2%-4.5%] 覆盖 VXX (中位数 2.86%)
2. **V49 → HEAD 回归 -27pp 中, `ENTRY_MAX_WAIT_BARS` 12 → 1.5 是部分原因**: ff4fdbf 把窗口从 12 → 1 (后 e0d0314 修正到 1.5, 仍远小于 V49 的 12)
3. **多标的分散**: `bot_factory.build_multi_symbol_bots` 基础设施已就位, 没启用. 两个同源 (UVXY+VXX) 标的 50/50 并行能降 MDD + 抓两个标的的 alpha

用户的核心担心: **参数过拟合于单一标的**. V49 参数是 UVXY 324 组 grid search + walk-forward 调出来的, 直接代到 VXX 是迁学. VXX 5y +226% 不能在没有 walk-forward 验证前作为决策依据.

---

## 2. 命题

**目标**: 三步串行, 每步 gate 下一步, 把当前默认 +82.81% (UVXY 4h) 推高到 ≥ +150% (合并多标的 + 恢复 V49 default), 且每步独立可证明非过拟合.

**成功标准**:
- Step 1 (VXX walk-forward) 通过 gate ≥ 4/6 窗口 valid ret > 0 → 证明 VXX 不是单一窗口幸运
- Step 2 (V49 default 恢复) UVXY 5y TURBO=OFF ret 上升 + test.py 全绿 → 部分恢复 -27pp 回归
- Step 3 (多标的 50/50) 合并 5y ret ≥ Step 2 后的 UVXY 单标 ret + MDD ≤ 单标的最大 MDD → 多标的至少不劣于单标的

**整体可接受结果**:
- Best case: 多标的合并 ret ~ +150-200% (UVXY 单标 +90-110% + VXX 单标 +200-230% 加权 50/50, 假设 walk-forward 不崩)
- Worst case: Step 1 gate 失败 (VXX 不稳定), 终止 D 路径, 保持当前 default

---

## 3. 三步设计

### Step 1 — VXX walk-forward 验证 [GATE, ~30 min]

**目的**: 检验"VXX 5y +226.79% 不是过拟合 / 不是单一窗口幸运". 这是整个 D 路径的 gate.

**关键参数**: Step 1 用 **`ENTRY_MAX_WAIT_BARS=12`** (V49 default, 即 Step 2 后的最终值) 跑 walk-forward, **不用** 当前 HEAD default 1.5. 这样 walk-forward 验证的就是 Step 3 实际部署的参数集. 用 env override 实现: `ENTRY_MAX_WAIT_BARS=12 python scripts/walk_forward_fixed.py ...`.

**做法**:
- 锁定当前 V49 参数 (config.py 所有 ENTRY_*, GRID_*, EXIT_* 等), 仅 `ENTRY_MAX_WAIT_BARS` 用 env override 设为 12
- 数据: `data/vxx_4h.csv` (3066 bars, 2021-05-17 → 2026-05-14, ~4y 11mo)
- 窗口切分: **rolling 13-month 窗口, step 6.5 month**. 实现:
  - 起点 [0, 6.5, 13, 19.5, 26, 32.5] months from data start
  - 每窗口 13 months 长度
  - 最后窗口结束 32.5 + 13 = 45.5 months ≤ 59 months (数据长度), 可放下 6 个
  - 窗口间有 ~6.5 month overlap (rolling 而非 non-overlapping, 提升样本利用率)
- 新建 `scripts/walk_forward_fixed.py` (~80 行):
  - argparse: `--csv`, `--symbol`, `--capital`, `--interval`, `--window-days` (default 390 即 ~13mo), `--step-days` (default 195 即 ~6.5mo)
  - 从 CSV 起止时间自动算窗口数 (期望 6 个), 不需 hardcode
  - 用 `BacktestRunner` (复用 backtest.py BacktestRunner, 但截取 df 到对应窗口的 bars) 跑每个窗口
  - 不需 train 段 (fixed params, 不调参), 直接每窗口跑 valid backtest
  - 输出 CSV: `runtime/experiments/vxx_walkforward/results.csv` 含 `window_idx, start_date, end_date, days, ret_pct, sharpe, mdd, sessions`

**Gate 条件**:
- 严格: **≥ 4/6 窗口 valid ret > 0** (≥ 67% 窗口盈利)
- 并且: 6 窗口 ret 的中位数 > 0 (避免"4 个 +10% + 2 个 -50%" 这种被中位数否决的情形)

**输出决策**:
- 通过 → 继续 Step 2 (VXX 不是过拟合, 可作为多标的成员)
- 不通过 → 终止 D, 报告: "VXX 5y +226% 主要靠 specific window 幸运, 不能合入多标的". P9 决策保持, 默认 UVXY 单标的.

### Step 2 — V49 default 恢复 (~15 min)

**目的**: 修正 ff4fdbf 引入的 `ENTRY_MAX_WAIT_BARS` 12 → 1 改动, 部分恢复 -27pp 回归. **关键**: Step 1 已用 env override 验证 12 在 VXX 上稳定, 现在把它从 env override 变成 config default.

**前置**: Step 1 通过 (gate ≥ 4/6 + 中位数 > 0).

**做法**:
- 改 `config.py` L232 `ENTRY_MAX_WAIT_BARS` 默认 `"1.5"` → `"12"`
  - **注意**: env override 仍生效, 用户可继续用 `ENTRY_MAX_WAIT_BARS=1.5` 测试旧行为
- 跑 4 次 backtest 验证 (不带任何 env override, 让 default 生效):
  - UVXY 4h TURBO=OFF: 预期 ret 从 +82.81% 上升 (向 V49 +109.91% 靠近)
  - VXX 4h TURBO=OFF: Step 1 walk-forward 已验证 12 在 VXX 上稳定, 此处只需确认 5y 单窗口 ret ≥ +192.77%
  - UVXY 4h TURBO=ON (sanity check): 当前 +73.81%, 改动后不应大变 (战术化主路径几乎不触发, 入场过滤变化影响 minor)
  - VXX 4h TURBO=ON (sanity check): 当前 +165.41%, 同上
- 跑 `python test.py` 252/252 全绿确认

**Gate 条件** (Step 2 → Step 3):
- UVXY TURBO=OFF ret 严格 > 当前 +82.81% (说明 ENTRY_MAX_WAIT_BARS=12 在 UVXY 上正向)
- VXX TURBO=OFF ret ≥ 当前 +226.79% × 0.85 = +192.77% (允许 ≤15% 下降但不大跌, 因为我们要保留 VXX 的核心 alpha)
- test.py 全过

**回滚条件**:
- 若 UVXY ret 反而下降 (说明 ENTRY_MAX_WAIT_BARS 与 V49 worktree 还有其他差异联动)
- 或 VXX ret 暴跌 (说明 12 在 VXX 上是负面)
- 或 test 失败

→ 回滚 `config.py` 改动, 终止 D 路径, 报告异常

### Step 3 — 多标的 UVXY+VXX 50/50 并行 (~1 h)

**目的**: 加权两个同源标的, 降单一标的过拟合风险 + 利用相关性 < 1 的特性降 MDD.

**前置**: Step 1+2 都通过.

**做法**:

**Step 3.1 — 新建 `scripts/run_multi_backtest.py` (~80 行)**:
- argparse: `--symbols UVXY VXX`, `--csv data/uvxy_4h.csv data/vxx_4h.csv`, `--allocations 0.5 0.5`, `--capital 10000`, `--interval 4h`
- 用 `bot_factory.build_multi_symbol_bots(symbols, total_capital, allocations)`
- **关键问题**: bot_factory 的 build 用 `LiveClock` 默认, 回测需要换成 `HistoricalClock`. 实现:
  - 共享一个 `HistoricalClock`, 注入到所有 bots
  - 双 CSV 分别加载到对应的 `SimulatedExecutor`
  - 主循环: 按 strategy bar 顺序前进, 每个 tick 调所有 bot 的 `.step()`
  - 合并 PnL 计算: total_equity = bot1.equity + bot2.equity
  - 合并 MDD: max((peak_total - current_total) / peak_total)
- **bar 同步**: 两个标的的 4h bar 时间戳应该对齐 (都是美股 4h regular hours, 同一时区). 但实际 CSV 的 t 列起止不同 (UVXY 2021-01-04 → 2026-04-24, VXX 2021-05-17 → 2026-05-14). 选取**两者重叠区间** (2021-05-17 → 2026-04-24) 作为 backtest 窗口.

**Step 3.2 — 跑回测**:
```bash
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv \
    --allocations 0.5 0.5 --capital 10000 --interval 4h
```
预期: ~5-10 min (两个 backtest 串行执行)

**Step 3.3 — 多标的 walk-forward (验证多标的也不过拟合)**:
- 用同样的 6 窗口切分 (重叠区间 ~4y 10mo, 每窗口 ~9-10mo)
- 每窗口跑 multi-symbol backtest
- 输出 CSV: `runtime/experiments/multi_symbol_walkforward/results.csv`
- Gate 条件: ≥ 4/6 窗口合并 ret > 0

**输出**:
- `runtime/experiments/multi_symbol/uvxy_vxx_50_50.csv` (full window)
- `runtime/experiments/multi_symbol_walkforward/results.csv` (6 windows)
- 关键指标: 5y 合并 ret, Sharpe, MDD, 各 sub-bot ret 拆分

---

## 4. Deliverables

**Step 1**:
- `scripts/walk_forward_fixed.py` (新, ~80 行)
- `runtime/experiments/vxx_walkforward/results.csv` (新)

**Step 2**:
- `config.py` `ENTRY_MAX_WAIT_BARS` 默认 1.5 → 12 (single line change)
- 4 个 sanity backtest 输出 (不落 CSV, 直接报告)

**Step 3**:
- `scripts/run_multi_backtest.py` (新, ~80 行)
- `runtime/experiments/multi_symbol/uvxy_vxx_50_50.csv` (新, full window)
- `runtime/experiments/multi_symbol_walkforward/results.csv` (新, 6 windows)

**收尾**:
- `reports/multi_symbol_alpha.md` (新, 综合 report)
- `findings.md` F9 段 (D 路径总结)
- `progress.md` 会话 4 日志
- 视结果: README §5 数据更新 (若 D 通过), CLAUDE.md §5.1 默认回测命令更新 (若 default symbol 改 VXX, 视用户决策)

**不在 deliverable**:
- 不重写 backtest.py / grid_bot.py 等业务代码
- 不动 tactical_config / session_manager (战术化 OFF 状态保持)
- 不动 CLAUDE.md (用户明确要求)
- 不做 30/70 / 70/30 等其他 split 比例的 cherry-pick 探索

---

## 5. Gate 矩阵 (整体)

| Gate | 条件 | 失败后果 |
|---|---|---|
| Step 1 Gate | ≥ 4/6 windows valid ret > 0 AND 中位数 > 0 | 终止 D, 报告 VXX 不稳定, 保持当前 default |
| Step 2 Gate | UVXY TURBO=OFF ret > +82.81% AND VXX TURBO=OFF ret ≥ +192.77% AND test 全绿 | 回滚 config, 终止 D, 报告异常 |
| Step 3 Gate | ≥ 4/6 multi-symbol windows ret > 0 AND 合并 ret > Step 2 UVXY 单标 ret | 报告多标的不带来增益, 不推荐合入 default |

整体成功: 三个 Gate 全过 → 推荐用户改 default 为 multi-symbol UVXY+VXX 50/50

---

## 6. 边界 / 风险

| 风险 | 缓解 |
|---|---|
| VXX walk-forward 不稳定 (低 Sharpe 0.32 暗示回报集中在少数事件) | Step 1 gate 严格 ≥ 4/6 + 中位数 > 0; 若不过直接终止 |
| ENTRY_MAX_WAIT_BARS=12 在 VXX 上是负面 | Step 2 gate 检查 VXX ret 不大跌; 跌超 15% 则回滚 |
| 多标的 backtest 实现 bug (clock 同步 / equity 合并) | 实现时跑单标的退化测试: 只传 1 个 symbol, ret 应该等于 backtest.py 单标的输出 |
| 50/50 不是最优 split (cherry-pick 嫌疑) | spec 明确说"不做 split 比例搜索, 50/50 直接用". 防过拟合于 split 比例 |
| 多标的 walk-forward 失败但单窗口 5y +150% (gate 形式过) | Gate 严格要求 walk-forward 也过, 不允许单窗口绕过 |
| 重叠区间裁剪后实际窗口小于 5y | 接受, 在 report 中标注实际窗口 (2021-05-17 → 2026-04-24 ≈ 4y 11mo) |
| `bot_factory.build_multi_symbol_bots` 默认用 LiveClock, 需手动注入 HistoricalClock | 实现时 `clock=` 参数显式传; 测试 1 symbol 退化等价 |
| CLAUDE.md §9 死规矩: 业务模块不读 wall-clock | 全部走 HistoricalClock; scripts/run_multi_backtest.py 自检 |

---

## 7. 不在范围 (诚实声明)

- 不证明"多标的 + 战术化 = 更好" (战术化决策已 P9 锁定 OFF)
- 不证明"≥ 3 标的并行" 比 "2 标的并行更好" (避免组合爆炸 / cherry-pick)
- 不探索"non-VIX 标的多标的组合" (例如 UVXY+RIOT, UVXY+SOXL): 这些组合的标的相关性 ≈ 0, 但单标的回报负, 加权后期望负
- 不动 V49 其他参数 (GRID_*, EXIT_*, BB 等), 只动 ENTRY_MAX_WAIT_BARS
- 不证明"实盘多标的会与回测一致" — 实盘需要 paper account 至少 4 周对账 (CLAUDE.md §5.3 + README §7)
- 不做 V49 → HEAD -29pp 回归的剩余部分 (-27pp 中除 ENTRY_MAX_WAIT_BARS 之外的) 的 git-bisect

---

## 8. 工作量估算

| 阶段 | 时间 |
|---|---|
| Step 1: walk_forward_fixed.py 写 + VXX 跑 | ~30 min |
| Step 2: config 改 + 4 sanity backtest + test verify | ~15 min |
| Step 3.1: run_multi_backtest.py 写 + 单 symbol 退化测试 | ~45 min |
| Step 3.2: 多标的 5y 跑 | ~10 min |
| Step 3.3: 多标的 walk-forward (6 窗口) | ~30 min |
| 收尾: report + findings + progress + commit | ~30 min |
| **总计** | **~2.5-3 h** |

---

## 9. 验收

完成 D 路径 (假设所有 gate 通过) 后, 用户能拿到:
1. 新的最佳 default 方案: UVXY+VXX 50/50, 5y 合并 ret (期望 ~+150-200%)
2. Walk-forward 验证报告 (证明非过拟合)
3. V49 default 部分恢复 (`ENTRY_MAX_WAIT_BARS = 12`)
4. 完整证据链: VXX walk-forward CSV + multi-symbol walk-forward CSV + sub-bot 拆分
5. 战术化决策保持 OFF (不被本次工作触碰)

实盘上线 (CLAUDE.md §5.3 流程不动):
- 仍需 Paper 实跑至少 4 周对账
- `main.py` 走单标的 (`config.SYMBOL`) 还是多标的 (`orchestrator.MultiSymbolOrchestrator`) 由用户决策, 不在本 spec 范围内
