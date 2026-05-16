# Symbol × Interval 对比 + 调参 + 验证 设计 (2026-05-16)

## 1. 目标

在 UVXY / VXX / UVXY+VXX 50/50 三个 symbol option × 1h / 4h / 1d 三个 interval option 的 9-cell crosstab 上选出综合最优 cell, 然后对该 cell 调参 (防过拟合), 最后验证 tuned 参数稳健。

**用户原话**:
> 对比 uvxy 和 vxx 两个标的, 判断单标还是组合回报更优, 判断 1 小时还是 4 小时级别更优 (我认为尽管 vxx 在 4 小时级别表现最优, 但是交易次数太少), 针对决定好的标的和对应时间级别调参 (注意过拟合风险), 再次回测验证

**评价准则** (用户确认): Sharpe + MDD + 收益 综合, 不是单一总收益率。

## 2. Scope (用户确认)

- Symbols: UVXY / VXX / Multi 50/50 (固定 allocation, 不扫 30/70 等)
- Intervals: 1h / 4h / 1d 全部
- Cross-symbol sanity: 包含 (Phase 3.5 — UVXY 上验证 VXX-tuned 参数, 反之亦然)
- 5y 全期 + Walk-forward 8 windows 是基本要求, 不加 cost stress test (留作独立 spec)

## 3. 当前已有数据 (重构后实测, 5y, $10k capital)

| Symbol/Combo | Interval | Ret | 年化 | Sharpe | MDD | Trades |
|---|---|---|---|---|---|---|
| UVXY | 4h | +82.81% | +12.05% | 0.32 | 15.86% | (~) |
| VXX | 1h | -20.04% | -4.38% | -0.667 | 36.13% | 326 |
| VXX | 4h | +226.79% | +26.78% | 0.321 | 15.17% | 279 |
| VXX | 1d | +268.41% | +29.84% | 0.428 | 17.35% | 64 |
| Multi 50/50 | 4h | +153.93% | +20.78% | (compute) | 15.09% | (combined) |

**Phase 1 需要新跑 5 cells**: UVXY 1h, UVXY 1d, Multi 1h, Multi 1d. (Multi 4h 已有)

## 4. Phase-by-Phase 设计

### Phase 1: 9-cell Crosstab

**输入**: data/{uvxy,vxx}_{1h,4h,1d}.csv (全部已存在)

**步骤**:
1. 跑 5 个缺失 backtest:
   - `python backtest.py --csv data/uvxy_1h.csv --interval 1h --capital 10000`
   - `python backtest.py --csv data/uvxy_1d.csv --interval 1d --capital 10000`
   - `python scripts/run_multi_backtest.py --symbols UVXY VXX --csv data/uvxy_1h.csv data/vxx_1h.csv --allocations 0.5 0.5 --capital 10000 --interval 1h --label phase1_multi_1h`
   - `python scripts/run_multi_backtest.py --symbols UVXY VXX --csv data/uvxy_1d.csv data/vxx_1d.csv --allocations 0.5 0.5 --capital 10000 --interval 1d --label phase1_multi_1d`
   - Multi 4h 已有, 跳过
2. 整理 9-cell 表格: Ret / 年化 / Sharpe / MDD / Trades

3. 每 cell 跑 walk-forward 8 windows (`scripts/walk_forward_fixed.py`):
   - 输出每 cell 的 pass rate (positive / 8)
   - 中位 ret
   - 最差 window ret

4. 整合到 `runtime/experiments/symbol_interval_2026-05-16/crosstab.csv`

**预算**: ~30-60 min (5 backtest × ~30s + 9 walk-forward × ~5min)

### Phase 2: Decision Gate

**输入**: Phase 1 crosstab.

**Decision rule** (按顺序应用):

1. **排除不稳定 cells**: walk-forward pass rate < 50% (< 4/8 positive) → 剔除
2. **排除 ret 负**: 5y full ret < 0 → 剔除
3. **排序剩余 cells**: 按 `score = annualized_ret × walkforward_pass_rate / MDD`
4. **Tie-break** (差距 < 5% 时):
   - Sharpe 更高
   - MDD 更低
   - Trades 更多 (统计可靠)
5. **Winner = score 最高**

**输出**: `(winner_symbol, winner_interval, baseline_metrics)`

### Phase 3: Tuning

**目标**: 在 winner (symbol, interval) 上找比 baseline 更优的参数, 同时防过拟合。

**Tool**: 现有 `scripts/tune.py` (5-param FULL_GRID for 1h/4h, GRID_1D for 1d)

**参数空间** (FULL_GRID for 1h/4h):
- ENTRY_MAX_ADX: [15, 20, 25]
- ENTRY_MAX_ATR_PCT: [0.035, 0.045, 0.055]
- GRID_SPACING_ATR_MULTIPLIER: [0.40, 0.50, 0.60]
- GRID_RECENTER_THRESHOLD_ATR: [0.8, 1.0, 1.2]
- EXIT_MAX_ADX: [20, 22, 25, 28]
- = 324 combos

**参数空间** (GRID_1D for 1d):
- ENTRY_MAX_ADX: [20, 25, 30]
- ENTRY_MAX_ATR_PCT: [0.07, 0.09, 0.11]
- GRID_SPACING_ATR_MULTIPLIER: [0.40, 0.50, 0.60]
- GRID_RECENTER_THRESHOLD_ATR: [0.8, 1.0, 1.2]
- EXIT_MAX_ADX: [25, 30, 35]
- = 243 combos

**步骤** (tune.py 内置):
1. Grid search 5y full → search_log.csv
2. Top-N (默认 8) → walk-forward 8 windows → walkforward.csv
3. Top-N → 3-split OOS (train+val+oos) → oos_report.csv
4. 最终选定参数 → ±20% stability 扰动测试 → stability.csv
5. 综合 → FINAL.json

**Overfitting 防御 (tune.py 内置)**:
- Walk-forward: ≥ 5/8 windows positive 才进 stability stage
- OOS: oos ret 不能 << val ret (`oos_ret >= val_ret * 0.7` 经验阈值)
- Stability: 参数 ±20% 扰动后 ret 退化 < 30%

如果 winner 是 Multi, tune.py 当前仅支持 single-symbol grid search — 需 Phase 3 前评估:
- Option A: 把 winner_symbol 取 Multi 时, 退化为对 dominant_symbol (Multi 内的主导贡献者) tune
- Option B: 接受 Multi 不调参, baseline 即终态
- 决策时报告并由用户选

**步骤 3.5: Cross-symbol sanity** (我加的):
- 把 FINAL.json 参数应用到另一个 symbol (e.g. VXX-tuned → UVXY) 跑 5y full
- 记录 cross_ret 与 baseline_uvxy_ret 的 diff
- 用于 Phase 4 Gate 5 决策

### Phase 4: Verify

**Tuned 参数应用方法**: 用 env override / config 修改运行 tuned params。

**Gate 通过条件** (全部满足):
1. Sharpe ≥ baseline_sharpe (winner 在 Phase 1 的值)
2. MDD ≤ baseline_MDD × 1.1 (允许略升 ≤10%)
3. Annualized ret ≥ baseline_annualized × 1.05 (至少 5% 提升)
4. Walk-forward ≥ 6/8 positive (比 baseline 不降低)
5. Cross-symbol sanity: cross_ret 与 baseline_other_ret diff > -30pp (即另一 symbol ret 不大幅退化)

**Gate fail 处理**: 不修 config 默认, 写诊断报告说明哪一项 fail + 推荐 next step。

**Gate pass 处理**: 修 config.py 默认 + 更新 README §5。

## 5. 产物

- `runtime/experiments/symbol_interval_2026-05-16/crosstab.csv` (Phase 1)
- `runtime/experiments/symbol_interval_2026-05-16/walkforward_summary.csv` (Phase 1)
- `runtime/experiments/<winner_symbol>_<winner_interval>/FINAL.json` (Phase 3 tune.py 产物)
- `runtime/experiments/symbol_interval_2026-05-16/cross_symbol_sanity.csv` (Phase 3.5)
- `runtime/experiments/symbol_interval_2026-05-16/phase4_verify.csv` (Phase 4)
- `reports/symbol_interval_tuning_2026-05-16.md` — 完整 4 phase 报告 + 决策日志
- (if Gate pass) `config.py` 修 + `README.md` §5 修 + `CHANGELOG.md` 追加

## 6. 验收清单

- [ ] Phase 1 9 cells 全部跑完 + crosstab 落盘
- [ ] Phase 1 walk-forward 9 cells × 8 windows 落盘
- [ ] Phase 2 decision rule 应用, winner 选出, 文档化原因
- [ ] Phase 3 tune.py 跑完 winner cell, FINAL.json 产出
- [ ] Phase 3.5 cross-symbol sanity 落盘
- [ ] Phase 4 Gate 5 项全检, 通过/失败 文档化
- [ ] (if pass) config.py + README.md + CHANGELOG.md 更新
- [ ] reports/symbol_interval_tuning_2026-05-16.md 完整 6 节 (Phase 1-4 + 数据/结论)

## 7. 不在 scope 内

- 不扫 Multi allocations (30/70 / 70/30 等) — YAGNI, 50/50 baseline 已 established
- 不做 cost sensitivity (BT_SLIPPAGE / BT_FILL_PROB 扫描) — 独立 spec
- 不修 V49 → 8c41c62 -27pp 回归 (commit e369447) — 独立 spec (T8 已定位)
- 不做新的标的筛选 (RIOT/SOXL/MARA 等) — D5 已扩展验证过, archived
- 不实盘 paper account 对账 — CLAUDE.md §7 user manual step

## 8. 风险 + 缓解

| 风险 | 缓解 |
|---|---|
| Phase 1 多 cell 跑慢 | walk-forward 用 8 windows 已是上限; 接受 30-60 min 一次性投入 |
| Multi winner 时 tune 无法用 single tune.py | Phase 3 前评估, Option A/B 报告由用户选 |
| Phase 3 tune.py 过拟合 | tune.py 内建 walk-forward + OOS + stability; 加 Phase 3.5 cross-symbol sanity |
| Gate 太严, 一个改进都不通过 | Gate fail 时只是"留 baseline + 诊断报告", 不损失现有能力 |
| Gate 太松, 过拟合参数被采纳 | Gate 5 条要全过, 含 walk-forward pass rate 和 cross-symbol sanity |
| Tuned 参数实盘行为与回测背离 | CLAUDE.md §7 优先级 1 (paper ≥ 4 周对账) 仍是 prereq |

## 9. Spec 自检

✅ Placeholder scan: 数值都是 Phase 1 之后才能填的, 但 spec 框架不含 TBD/TODO
✅ 内部一致性: Phase 1-4 input/output 清晰, decision rule 唯一
✅ Scope: 单 spec 大小适中, ~6-12h 实际工作
✅ 二义性: Gate 5 条数学上无歧义, decision rule tie-break 顺序明确
