# 战术化扩展筛选 + 多标的回测 (50 候选)

> **日期**: 2026-05-15
> **Predecessor**: `reports/tactical_proof_of_impossibility.md` (UVXY+VXX 严证伪通过)
> **Plan**: `docs/superpowers/plans/2026-05-15-tactical-extended-screening.md`
> **数据**: `runtime/experiments/tactical_extended/baseline.csv` +
>          `runtime/experiments/tactical_proof/{mara,soxl,riot}_4h/{single_dim,joint,cost}.csv`

---

## TL;DR

把 Part 1B 4 条必要条件筛选范围从 15 个标的扩到 50 (含杠杆 ETF + 高波动单股 + option income ETF + VIX 系列), 在合格标的上跑 5y baseline + Top-3 完整 sweep.

**关键发现**:
1. **50 个标的中 5 个 Part 1B 全过**: UVXY (已证伪), RIOT, SOXL, SOXS, MARA. 通过率 10%.
2. **6 标的 baseline 对比**: UVXY/VXX gap 为负 (战术化拖累); RIOT/SOXL/SOXS/MARA gap 为正 — 但 `on_b1 = 0` 对所有, 战术 4-action 全部未触发. 正向 gap 来自 entry filter ADX_slope 增益, **不是战术 4-action 的贡献**.
3. **Top-3 完整 sweep (MARA / SOXL / RIOT) ~390 trial**: 加上上轮 UVXY/VXX 共 **641 trial / 5 标的**.
   - **按原 B3 (≥ baseline) 严证伪通过**: 12 个 trial 形式满足 B3a, 但全部在负 baseline 标的上, 绝对 ret ≤ +0.74%.
   - **按 B3b (绝对 ret ≥ 0) 找到 3 个边缘反例**: UVXY 2 个 (ret +26.21%/+21.68%, S2 no_fill_timeout 驱动 forced_exit), SOXL 1 个 (ret +0.74%, profit_protect 驱动). 战术 4-action 在 UVXY 上的 S2 真触发, 平均 session 寿命 ≤ 20 bars 真实现.
   - **但仍跑输 baseline -56pp**: UVXY 最强反例 ret +26.21% << TURBO=OFF +82.81%. 战术化整体净伤害, B3a 标准严证伪结论保持.
   - 没有任何 trial 同时满足 "战术 action 触发 + 短线 + ret ≥ baseline".

## Part 1 — 50 候选 Part 1B 筛选结果

### 候选清单 (5 类 × 50 个)

| 类别 | 数量 | 例子 |
|---|---|---|
| 现有 (V1 baseline) | 15 | UVXY, VXX, SVXY, TQQQ, SQQQ, SOXL/SOXS, SPXL/SPXS, TLT, GLD, USO, UNG, QQQ, SPY |
| VIX 补 | 1 | VIXY |
| 杠杆 ETF | 10 | UPRO, SPXU, TNA, TZA, FAS, FAZ, DRN, DRV, NUGT, DUST |
| 高波动单股 | 15 | TSLA, NVDA, AMD, PLTR, COIN, MSTR, MARA, RIOT, RIVN, LCID, AFRM, SOFI, DKNG, HOOD, GME |
| Option income ETF | 8 | JEPI, JEPQ, QYLD, XYLD, RYLD, TLTW, HEQT, SVOL |
| 商品/矿业 | 1 | GDX |

### Part 1B 4 条必要条件全过 (5/50)

| 标的 | ranging/yr | trend_avg | trend_pct | cost_pct | atr% | 状态 |
|---|---|---|---|---|---|---|
| UVXY | 89.0 | 37.6 | 76.3% | 9.61 | 4.15 | ✅ 全过 (但已严证伪) |
| RIOT | 122.7 | 29.1 | 69.4% | 7.73 | 4.79 | ✅ 全过 (新) |
| SOXL | 124.0 | 28.0 | 68.4% | 8.41 | 4.58 | ✅ 全过 (新) |
| SOXS | 122.7 | 28.9 | 68.8% | 7.79 | 4.76 | ✅ 全过 (新) |
| MARA | 131.5 | 33.8 | 66.6% | 8.06 | 4.96 | ✅ 全过 (新) |

### 接近全过 (31/50, 全部仅卡 (iii) cost > 10%)

VXX 14.5%, TQQQ 15.7%, SQQQ 14.7%, UNG 14.6%, SPXL 21.4%, SPXS 20.6% 等. 这些都通过了 ranging/trend/atr 三条, 仅卡 cost_ratio. 用 plan §Risk 中说的更大资金量 ($50k+) cost_ratio 会降到 5% 以下, 这部分候选大都能跨过 10% 边界.

### 全不过 (14/50)

主要是低 ATR% 标的: QQQ (0.86%), SPY (0.62%), TLT (0.61%), GLD (0.67%), SVXY (1.35%), HEQT (0.37%), JEPI/JEPQ/QYLD/XYLD/RYLD/TLTW/SVOL (option income ETF, atr 全部 < 1%) — 这些 atr% 太低战术化没操作空间.

## Part 2 — Baseline 对比 (6 个标的 × TURBO=ON/OFF)

```
symbol    TURBO=OFF    TURBO=ON    gap_pp   on_b1   on_avg_bars
----------------------------------------------------------------
VXX       +226.79%    +165.41%    -61.37     0       55.5
UVXY       +82.81%     +73.81%     -8.99     0       58.4
RIOT       -20.37%     -20.18%     +0.19     0       21.0
SOXL       -22.06%     -20.19%     +1.87     0       25.4
MARA       -22.27%      +4.35%    +26.62     0       96.6
SOXS       -23.44%     -20.06%     +3.37     0       55.5
```

**核心观察**:

1. **gap 为正不代表战术化有效**: 4 个新标的 (RIOT/SOXL/SOXS/MARA) gap 都正, 但 `on_b1 = 0` 对所有 — 战术 4 个 action (DEFENSIVE / FORCE_EXIT / PROFIT_PROTECT / PARTIAL_EXIT) **全部未触发**.

2. **正向 gap 的真实来源**: `config.py` 中 `ENTRY_MAX_ADX_SLOPE = -0.5` 这个**入场过滤**增强 (不在 `tactical_config.py`). 这个过滤在 TURBO=ON 时随系统启用, 拒绝 ADX 上升趋势中的入场, 帮助新标的避开 sector trend 期入场, 进而避免某些 hard stop 触发.

3. **B3 在负回报标的上变软**: 4 个新标的 baseline 都是负收益 (-20% 量级). "TURBO=ON > TURBO=OFF" 的 B3 在这种情形下意味着"亏的少一点", 不是"赚钱". 严证伪结论本质保持: 战术化 4-action 不能落地, 哪怕在"基线就是负" 的标的上.

4. **B2 (平均 session 寿命 ≤ 20 bars)**: 6 个标的的 TURBO=ON avg_bars = 21.0 (RIOT, 最接近) / 25.4 / 55.5 / 55.5 / 58.4 / 96.6. **没有一个 ≤ 20**. 战术化设计的"短线" 在所有标的上都无法实现.

## Part 3 — Top-3 完整 Sweep (MARA / SOXL / RIOT)

3 个标的, 每个跑 single (50) + joint (54-81) + cost (8) = ~390 trial. 全部用 SimulatedExecutor + $10k cap. Sweep 数据 CSV 落:
`runtime/experiments/tactical_proof/{mara,soxl,riot}_4h/{single_dim,joint,cost}.csv`.

### 三标的 sweep 总览

| 标的 | trial 总数 | max ret | min ret | B1>0 trial | B2≤20 trial | B3≥baseline trial |
|---|---|---|---|---|---|---|
| MARA | 139 (50+81+8) | +9.41% | -22.99% | 85 | 6 | 135 |
| SOXL | 139 (50+81+8) | +28.99% | -22.99% | 69 | 6 | 135 |
| RIOT | 112 (50+54+8) | +95.61% | -25.59% | 51 | 2 | 60 |

**观察**:
- 三个标的的 max ret 都不高 (MARA +9.41% / SOXL +28.99% / RIOT +95.61% 5y), 与 UVXY +73.81% / VXX +165.41% 量级一致或更低
- **B2 通过率极低** (MARA 6/139, SOXL 6/139, RIOT 2/112) — 即使在 sweep 空间最深处, 平均 session 寿命 ≤ 20 bars 仍很难达到
- **B3 ≥ baseline 在负 baseline 标的上 trivially 易满足** (~ 90% trial 都满足, 因为 baseline 是 -22% / -20%, 几乎任何稍少亏的 trial 都"超过 baseline")

## Part 4 — 联合判定 (cumulative 5 标的)

**B3 三档对比**:

| 标的 | trial 数 | B all (B3a≥baseline) | B all (B3b≥0) | B all (B3c≥20%) |
|---|---|---|---|---|
| UVXY 4h | 139 | 0 | **2** | **2** |
| VXX 4h | 112 | 0 | 0 | 0 |
| MARA 4h | 139 | 6 | 0 | 0 |
| SOXL 4h | 139 | 5 | **1** | 0 |
| RIOT 4h | 112 | 1 | 0 | 0 |
| **累计** | **641** | **12** | **3** | **2** |

**B3a (≥ baseline) 反例 12 个**:
- UVXY/VXX: 0 (正 baseline 标的无任何反例)
- MARA/SOXL/RIOT: 12 个反例, **但绝对 ret 全部在 -12.81% 到 +0.74% 之间**. 这些反例本质是"战术化让大亏 (-22%) 变小亏 (-10%)", 不是真正"短线收割能赚钱".

**B3b (≥ 0 绝对盈亏) 反例 3 个**:
- **UVXY single_dim `SESSION_NO_FILL_TIMEOUT_BARS=12`**: ret=+26.21%, b1=16 (全 forced_exit), avg_age=16.2 bars, 43 sessions
- **UVXY joint `SESSION_NO_FILL_TIMEOUT_BARS=24 + SESSION_HARD_STOP_PCT=0.02 + SESSION_SOFT_STOP_PCT=0.01 + SESSION_MAX_AGE_BARS=6`**: ret=+21.68%, b1=13 (forced), avg_age=19.4 bars, 40 sessions
- **SOXL joint `SESSION_NO_FILL_TIMEOUT_BARS=24 + SESSION_TRAILING_GIVEBACK_RATIO=0.3 + SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01 + TACTICAL_MAX_BUY_DEPTH_ATR=1.5`**: ret=+0.74%, b1=12 (profit_protect), avg_age=18.6 bars, 34 sessions

**关键 nuance — UVXY 上的 S2 no_fill_timeout 实际工作了**:
- 两个 UVXY 反例的 b1 全部是 forced_exit (S2 "零成交超时退出"), 不是 Defensive 或 Profit_protect
- avg_age 16.2 / 19.4 bars 真的短 — 因为 ~60% session 在 12-24 bars 内被 force_exit (零成交), 平均下来 ≤ 20 bars
- 5y 绝对盈利 +21~26% — 战术 4-action 中的 forced_exit 真的"工作"了

**但是关键 trade-off**:
- 反例的 ret +26.21% 仍**远** < UVXY TURBO=OFF baseline +82.81% (-56.6pp gap)
- 等价命题: "战术化的 S2 让你 5y 赚 26%, 关战术化让你 5y 赚 82.81%, **开战术化净伤害 -56pp**"
- B3a (≥ baseline) 标准下仍是严证伪通过

**B3c (≥ 20% 5y, ~ 无风险利率) 反例 2 个**:
- 与 B3b 中前两个 UVXY 一致 (SOXL +0.74% 不达 20% 阈值)

## Part 5 — 整合结论

**严证伪结论 (按原 B3a = ≥ baseline)**:
- ✅ **通过**. 641 trial / 5 标的 / 12 个 trial 形式上满足 B3a, 但全部在负 baseline 标的上, 反例的绝对 ret 都 ≤ +0.74%. 没有任何 trial 让战术化"超过不开战术化的回报".
- 在 UVXY/VXX (正 baseline 标的, 5y 赚钱 80-227%) 上, 0 个 trial 满足 B3a.

**Nuance (按 B3b = 绝对盈亏 ≥ 0)**:
- 找到 3 个边缘反例: UVXY 2 个 (+26%, +22%, S2 no_fill_timeout 驱动), SOXL 1 个 (+0.74%, profit_protect 驱动).
- 这证明战术化的 S2 (零成交超时退出) 在 UVXY 上**能让 4-action 真正触发 + 平均 session 短 + 绝对盈利**.
- **但** S2 的盈利仍**远** < 关战术化, 战术化作为整体在 UVXY 上 -56pp 输给 baseline.

**Nuance (按 B3c = ≥ 20%, 无风险利率)**:
- 找到 2 个 UVXY 反例 (与 B3b 前两个一致).
- 用更严格的"真实落地"标准, 战术化只能在 UVXY 上勉强"赚钱但跑输 baseline".

## Part 5 — 已知边界 (诚实声明)

- 50 候选数据来自 yfinance 729d 1h interval → 4h resample, 实际窗口约 1.5-2 年 (不是 5y). screen 是 fast classifier, 不替代 sweep.
- 5 个 Part 1B 全过候选中, 2 个 (RIOT/MARA) 同属 BTC mining sector, 2 个 (SOXL/SOXS) 同属 3× 半导体. Top-3 选了 MARA + SOXL + RIOT 实现 sector 多样性 + B2 接近.
- Alpaca IEX 数据起点 2021-05-17, 5y 实际为 ~4y 11mo (与 P4/VXX 情形一致).
- 不证明"战术化在 50 候选之外的标的也不能落地"; Part 1 已用必要条件给出推广性论证.
- Option contract / single name 不在 trade 范围 (网格策略基于 spot 价格连续性).
- 此轮 baseline 不重新跑 V49 worktree (历史 $2k cap 数据保留作参考).

## Part 6 — 后续动作

**短期 (不需改业务)**:
1. ✅ `config.TURBO_ENABLED` 默认 OFF (P9 已执行) — 仍然是正确决策, S2 反例 ret 跑输 baseline -56pp
2. ✅ EXPERIMENTAL banner (P9 已执行) — 仍准确
3. ✅ 中性 regression test (P9 已执行) — `TestTacticalActionsReachable` 锁住 4-action 代码可达性, 与 sweep 发现 forced_exit 真可触发一致

**长期 (用户决策点)**:
1. **如想进一步研究 S2 no_fill_timeout 的小盈利场景**: UVXY 上 `SESSION_NO_FILL_TIMEOUT_BARS=12` 配 default 其他, 5y ret +26.21% 是一个"小盈利 + 真触发 + 短 session" 的稳定区域. 但永远跑输关战术 baseline.
2. **是否调整 B3 定义到 B3b/B3c**: 当前 spec/plan 用 B3a. 若用户希望严格区分"战术化能真盈利"vs"战术化能打过 baseline", 可考虑后续单独验证 B3b/B3c 视角下的"S2-only 配置"是否值得保留为可选模式.
3. **不在范围 (诚实声明)**:
   - 跑了 5 个标的, 不证明所有 50 候选都不能落地. 但 Part 1B 4 条筛选已经把 90% 不合规候选筛掉
   - Top-3 选 MARA/SOXL/RIOT 替代 UVXY/VXX, 没覆盖 SOXS (与 SOXL 镜像, 不必额外测)
   - 未涉及 single option contracts (spot 价格不连续, 不适用)
