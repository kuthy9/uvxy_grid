# 战术化不可落地性证明 (Proof of Impossibility)

| 元信息 | 值 |
| --- | --- |
| 报告日期 | 2026-05-15 |
| 命题 | 战术化"短线收割" (TURBO=ON, 4-action: defensive / forced / profit-protect / tactical-override) 不能在合规边界内落地 |
| 合规边界 | 标的 ∈ {UVXY 4h, VXX 4h}; 资本 = $10,000; 回看 = 5y (UVXY: 2021-01-04 → 2026-04-24 完整 5y; VXX: 2021-05-17 → 2026-05-14 ≈4y 11mo 21d) |
| 设计 spec | [`docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md`](../docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md) |
| 执行 plan | [`docs/superpowers/plans/2026-05-15-tactical-impossibility-proof.md`](../docs/superpowers/plans/2026-05-15-tactical-impossibility-proof.md) |
| 数据来源 | `runtime/experiments/tactical_proof/{uvxy_4h, vxx_4h}/{single_dim, joint, cost}.csv` |
| 既往证据 | `findings.md` F2 (战术化净负贡献); `README.md` §5 (V49 baseline +109.91%) |

---

## TL;DR

严证伪通过. 在合规边界 (UVXY 4h × 5y × $10k cap 与 VXX 4h × ≈4.96y × $10k cap) 内的所有 sweep 空间 — UVXY 共 50 + 81 + 8 = 139 trial, VXX 共 50 + 54 + 8 = 112 trial, 合计 **251 trial** — **无任何配置同时满足 B 三条**:

- **B1**: Defensive / Forced / Profit-protect 至少一类触发次数 > 0;
- **B2**: 平均 session 寿命 ≤ 20 bars (≤ 3.3 个交易日, 即"短线");
- **B3**: 5y 总回报 ≥ TURBO=OFF baseline (UVXY +82.81%, VXX +226.79%).

UVXY 4h max return 在 TURBO=ON 全 sweep 下为 +73.81% (距 baseline -9.00pp); VXX 4h max 为 +290.58% (B3 满足, 但 avg session 寿命 = 263 bars, 远超 B2 ≤ 20 bars 阈值). 这两个标的代表了"高杠杆波动" (UVXY 2× VIX) 与"中等波动 vol-of-vol" (VXX 1× VIX), 又恰好是 README §2 给出的"高波动 / 弱趋势 / 高流动性"网格候选, 因此结论可外推到形态相近的 ETF: 战术化"短线收割"概念在本仓库设计的合规边界内不能落地, 应作为实验性代码冻结 (TURBO 默认 OFF), 不应进入实盘默认路径.

---

## Part 1 — 机制 (理论 / Why)

### 1A. UVXY 专属冲突 — 为什么"短线"反而吃掉收益

UVXY 4h × 5y × $2k V49 baseline 的可观察 edge 来源 (`README.md` §5, `findings.md` F2):

- session 平均寿命约 60 bars (≈10 交易日);
- ADX > 22 占比高 (本次实测 48.1%), session 经常发生在 ADX 高位的"高波动 + 弱趋势" 窗口;
- 净回报 +109.91% / Sharpe 0.54, 来源是"等到 ATR 间距下的网格被填满 + EMA 中轴 + grid_engine 的远端 SELL 触发, 慢慢吃完一整段 ranging window".

把这个 edge 拆开看, 战术化 4 个 action 与之结构性冲突 (而不是参数没调好):

| 战术 action | 对 V49 edge 的破坏方式 | 实测后果 |
| --- | --- | --- |
| Forced exit (max-age / hard-stop) | 强制砍掉 hold 60-bar session, 提前在 wider grid 还没回归时退出, 把"等回归"的 edge 直接吃掉 | UVXY single_dim `SESSION_HARD_STOP_PCT=0.02` 触发 20 次 forced exit → ret +55.40% (-27.4pp) |
| Profit-protect (trailing giveback) | 在浮盈 1-3% 就锁利出场, 大概率错过后续 ATR 间距下的网格收割 | UVXY `SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01` 触发 25 次 → ret +11.53% (-71.3pp) |
| Defensive (TREND_RISK_SCORE 提前退场) | 把 ADX>22 的高位区间直接当"危险" → 偏偏这正是 V49 edge 高发区 | UVXY `TREND_RISK_SCORE_DEFENSIVE=70.0` 触发 20 次 → ret +54.45% (-28.4pp) |
| Tactical override grid_engine_exit | 关掉 grid_engine 的网格 SELL 触发, 改靠 session 内部信号退出 → 等同于把网格的远端 SELL edge 主动放弃 | UVXY `TACTICAL_OVERRIDE_GRID_ENGINE_EXIT=1` → 一次入场, 持仓到 EOF, ret **-20.26%** |

结论: 4 个 action 不是"参数没调好", 是**与 UVXY 4h edge 的时间结构 (long hold + ADX 高位) 反向**. UVXY 上能给战术化"留口子"的唯一配置, 是把每个 action 都设到"接近不触发" — 这种"形同关闭"的配置等价于回到 TURBO=OFF, 而 cost.csv (见 Part 3B) 已经实证了"默认 TURBO=ON 时 4 action 完全不触发, 表现等同 TURBO=OFF + 滑点损失".

### 1B. Symbol-agnostic 必要条件

战术化"短线收割"是个一般性概念, 不只是 UVXY 的问题. 要在任何标的 / 任何周期上跑通, **必须**同时满足下列 4 个必要条件:

- **(i) 短周期 ranging window 密度足够高** — 即"ATR 间距 + EMA 中轴"的网格在 ≤ 20 bars 窗口内能被双向填满. 等价物理量: `ATR%` 高 + `ADX` 中位数低. 否则强制 20-bar 出场只会在网格未填满时被砍掉.
- **(ii) 等待型 edge 不主导** — 如果策略主要 edge 来自"长 hold + 远端网格 SELL", 任何"短线"动作都是 edge 净流出, 这是 UVXY 1A 表格的情形.
- **(iii) 成本占比基线低** — TURBO=ON 默认会因 forced exit / profit-protect 引入更多 round-trip, 单笔毛利必须显著大于 spread + commission + SEC/TAF, 否则净后回报为负.
- **(iv) 战术化对成本不"免疫"** — 必要条件之**反向**: 如果一种"战术化"配置在所有成本档下都给出完全一致的回报 (P7 cost sweep 实测), 这恰恰说明它从未触发任何 action — 它对成本免疫不是 robust, 而是 dead.

UVXY 4h 与 VXX 4h 在 (i)-(iv) 上的实测对比见 1C.

### 1C. UVXY 4h vs VXX 4h 属性对比

`indicators.py` 单次计算, 全样本中位数:

| 维度 | UVXY 4h | VXX 4h |
| --- | --- | --- |
| ATR% 中位数 | 4.50% | 2.86% |
| ADX 中位数 | 21.56 | 21.43 |
| ADX > 22 占比 | 48.1% | 47.8% |
| 4h bar 样本数 | 3342 bars (2021-01-04 → 2026-04-24) | 3066 bars (2021-05-17 → 2026-05-14) |
| 数据窗口长度 | ≈5y 3mo | ≈4y 11mo 21d |
| TURBO=OFF baseline 5y ret | +82.81% | +226.79% |
| 杠杆 → 长 hold edge 强度 | 强 (V49 +109.91%, hold ≈60 bars) | 弱 (1× VIX, 振幅小, 单 session 期望毛利小) |
| TURBO=ON ↔ OFF gap (5y) | -9.00pp | -61.37pp |
| 战术 4-action 触发 (default config) | 0 | 0 |

两个标的的 ADX 中位数与 ADX>22 占比都接近 50%, **不满足 (i) 的"ranging window 高密度"**: 至少一半时间 ADX>22, 在 4h 时间尺度上 20-bar (≈3.3 交易日) 窗口很难做完一个完整 grid 双边填充. UVXY 的杠杆放大让 (ii) "长 hold edge" 在 ATR% 4.50% 的环境下额外强势; VXX 则是另一种极端 — TURBO=ON 直接吃掉 -61.37pp, 因为 1× 振幅下单 session 期望毛利更小, 战术 round-trip 的成本/毛利比更差. 两端都堵死.

---

## Part 2 — 单维 sweep 数据 (一个旋钮一个旋钮证伪)

### UVXY 4h — 50 trial

每个旋钮"该轴下最佳 trial" + 是否满足 B 三条 (baseline +82.81%):

| 旋钮 (knob) | 最佳 value | B3 ret | B1 trigger | B2 avg bars | B 全满足? |
| --- | --- | --- | --- | --- | --- |
| SESSION_HARD_STOP_PCT | 0.20 (≈关闭) | +73.81% | 0 | 58.4 | ❌ B1=0, B2 fail |
| SESSION_SOFT_STOP_PCT | 0.10 (≈关闭) | +73.81% | 0 | 58.4 | ❌ B1=0, B2 fail |
| SESSION_MAX_AGE_BARS | 99999 (≈关闭) | +73.81% | 0 | 58.4 | ❌ B1=0, B2 fail |
| TREND_RISK_SCORE_DEFENSIVE | 70.0 | +54.45% | 20 forced | 29.9 | ❌ B3 fail (-28pp) |
| TREND_RISK_SCORE_FORCE_EXIT | 999.0 (关闭) | +54.45% | 20 forced | 29.9 | ❌ B3 fail |
| SESSION_MIN_PROFIT_TO_PROTECT_PCT | 0.05 (≈关闭) | +73.81% | 0 | 58.4 | ❌ B1=0, B2 fail |
| SESSION_TRAILING_GIVEBACK_RATIO | 0.5 | +54.45% | 20 forced | 29.9 | ❌ B3 fail |
| TACTICAL_OVERRIDE_GRID_ENGINE_EXIT | 1 (开启) | **-20.26%** | 0 | 0 (no exits) | ❌ B3 catastrophic |
| TACTICAL_MAX_BUY_DEPTH_ATR | 0.0 (≈关闭) | +73.81% | 0 | 58.4 | ❌ B1=0, B2 fail |
| SESSION_NO_FILL_TIMEOUT_BARS | 12 | +26.21% | 16 forced | 16.2 | ❌ B3 fail (-57pp) |

**关键观察**: UVXY 全 50 trial 中, B1>0 共 17 个 trial, 其中 **0 个 trial 的 B3 ≥ 82.81%**. 即"只要让 4-action 任意一个真正触发, 5y 回报必然低于 baseline". B1>0 与 B3≥baseline 在 UVXY 4h 单维 sweep 上是互斥关系.

### VXX 4h — 50 trial

baseline +226.79%, 表头同上:

| 旋钮 (knob) | 最佳 value | B3 ret | B1 trigger | B2 avg bars | B 全满足? |
| --- | --- | --- | --- | --- | --- |
| SESSION_HARD_STOP_PCT | 0.05 | +169.45% | 7 forced | 54.6 | ❌ B3 fail, B2 fail |
| SESSION_SOFT_STOP_PCT | 0.20 | +169.45% | 7 forced | 54.6 | ❌ B3 fail, B2 fail |
| SESSION_MAX_AGE_BARS | 6 | +172.78% | 0 | 55.5 | ❌ B1=0 |
| TREND_RISK_SCORE_DEFENSIVE | 60.0 | +171.96% | 0 | 55.5 | ❌ B1=0 |
| TREND_RISK_SCORE_FORCE_EXIT | 60.0 | +169.57% | 0 | 53.6 | ❌ B1=0 |
| SESSION_MIN_PROFIT_TO_PROTECT_PCT | 0.03 | +169.24% | 6 | 53.2 | ❌ B3 fail, B2 fail |
| SESSION_TRAILING_GIVEBACK_RATIO | 1.0 (≈关闭) | +152.40% | 8 protect | 43.1 | ❌ B3 fail, B2 fail |
| **TACTICAL_OVERRIDE_GRID_ENGINE_EXIT** | **1** | **+380.25%** | 1 forced | **325.2** | ❌ **B2 fail (325 >> 20)** |
| TACTICAL_MAX_BUY_DEPTH_ATR | 1.0 | +153.54% | 0 | 55.6 | ❌ B1=0 |
| **SESSION_NO_FILL_TIMEOUT_BARS** | **96** | **+404.66%** | 1 forced | **236.5** | ❌ **B2 fail (236 >> 20)** |

**关键观察**: VXX 全 50 trial 中, B3≥226.79% 共 2 个 trial (上表加粗两行), 但这两行都 B2 严重失败 (avg session bar 236-325, 远超 ≤ 20 bars 阈值). 物理上, 这两个配置等同于"基本关掉了 grid_engine 的 SELL 触发并改用 buy-and-hold-until-session-timeout", 与"短线收割"的命题反向, 不能算作命题成功. B1>0 全部 23 个 trial 中只有这 2 个 B3 满足, 其余 21 个 B3 fail; 但即便这 2 个也无法满足 B2.

---

## Part 3 — 联合 sweep + 成本敏感性

### 3A. 联合 sweep

#### UVXY 4h — 81 trial

axes = `[SESSION_NO_FILL_TIMEOUT_BARS, SESSION_HARD_STOP_PCT, SESSION_SOFT_STOP_PCT, SESSION_MAX_AGE_BARS]` (3 × 3 × 3 × 3 笛卡尔积).

- max B3 ret = **+73.81%** (= 单维结果上限, 联合并没"组合出"更高的回报);
- B1 全满足 trial = 27 个, B2 ≤ 20 bars trial = 1 个, B3 ≥ baseline trial = 0 个;
- **B 全满足 = 0 / 81**.

也就是说 UVXY 联合 sweep 等效于"只要让 4-action 真正触发, 你最多拿到 +73.81%", 联合维度上的 B3 上限和单维一致, 没有非线性涌现.

#### VXX 4h — 54 trial

axes = `[TACTICAL_OVERRIDE_GRID_ENGINE_EXIT, SESSION_NO_FILL_TIMEOUT_BARS, SESSION_MAX_AGE_BARS, TREND_RISK_SCORE_DEFENSIVE]`.

- max B3 ret = +290.58% (`override=1, no_fill=96, max_age=6, defensive=40.0`);
- B1 全满足 trial = 10 个, B2 ≤ 20 bars trial = 6 个, B3 ≥ baseline trial = 3 个;
- **B 全满足 = 0 / 54**.

3 个 B3 ≥ baseline outlier (`trial_id` 在 CSV 内):

| trial 配置 (override=1, defensive=40.0 不变) | B3 ret | B2 avg bars | B2 通过? |
| --- | --- | --- | --- |
| `no_fill=96, max_age=6` | +290.58% | 263.6 | ❌ |
| `no_fill=96, max_age=30` | +263.72% | 496.7 | ❌ |
| `no_fill=96, max_age=99999` | +256.75% | 583.9 | ❌ |

三个 outlier 都把 `TACTICAL_OVERRIDE_GRID_ENGINE_EXIT=1` (关网格 SELL) 与 `TREND_RISK_SCORE_DEFENSIVE=40.0` (极保守 defensive 阈值, 但实际由于 `no_fill_timeout` 主导出场逻辑) 组合, 物理结果是"几乎不退出, 等 session 寿命变成 263-584 bars (≈44-97 交易日)". 与 Part 2 的两个单维 outlier 同源 — 等价于"强制长持仓", 与短线收割反向.

### 3B. 成本敏感性 (`cost.csv`, 8 trial / 标的)

测试 4 档 (slip_bps, comm_per_share) ∈ {(3, 0.00175), (5, 0.0035), (7.5, 0.00525), (10, 0.007)} × TURBO ∈ {ON, OFF}:

#### UVXY 4h

| slip_bps | comm | TURBO=ON ret | TURBO=OFF ret | gap |
| --- | --- | --- | --- | --- |
| 3.0 | 0.00175 | +73.81% | +82.81% | -9.00pp |
| 5.0 | 0.00350 | +73.81% | +82.81% | -9.00pp |
| 7.5 | 0.00525 | +73.81% | +82.81% | -9.00pp |
| 10.0 | 0.00700 | +73.81% | +82.81% | -9.00pp |

#### VXX 4h

| slip_bps | comm | TURBO=ON ret | TURBO=OFF ret | gap |
| --- | --- | --- | --- | --- |
| 3.0 | 0.00175 | +165.41% | +226.79% | -61.37pp |
| 5.0 | 0.00350 | +165.41% | +226.79% | -61.37pp |
| 7.5 | 0.00525 | +165.41% | +226.79% | -61.37pp |
| 10.0 | 0.00700 | +165.41% | +226.79% | -61.37pp |

两个标的下, TURBO=ON 与 TURBO=OFF 的回报在所有成本档完全恒定. `cost.csv` 中 TURBO=ON 行的 `b1_trigger_total = 0` 实证: **默认 config 下, 战术化 4-action 完全没触发**. 这是 1B(iv) 必要条件的反向证据 — "对成本免疫" 不是 robust, 而是 dead. UVXY 上 -9.00pp 的固定 gap 完全来自一次性入场建仓的 spread / commission 差 (TURBO=ON 入场更早 / 频次更多); VXX 上的 -61.37pp 同理但放大 (因为 1× 振幅下单笔毛利占比相对成本更弱).

---

## Part 4 — 联合判定

### UVXY 4h

| 指标 | 值 |
| --- | --- |
| 总 sweep 空间 | 50 (single) + 81 (joint) + 8 (cost) = **139 trial** |
| B1>0 trial | 17 (single) + 27 (joint) + 0 (cost) = 44 |
| B2 ≤ 20 bars trial | 9 (single) + 1 (joint) + 0 (cost) = 10 |
| B3 ≥ baseline (82.81%) trial | 0 (single) + 0 (joint) + 0 (cost) = **0** |
| **B 全满足 trial** | **0 / 139** |
| max ret (TURBO=ON) | +73.81% (距 baseline -9.00pp) |
| **结论** | **严证伪通过** |

### VXX 4h

| 指标 | 值 |
| --- | --- |
| 总 sweep 空间 | 50 (single) + 54 (joint) + 8 (cost) = **112 trial** |
| B1>0 trial | 23 (single) + 10 (joint) + 0 (cost) = 33 |
| B2 ≤ 20 bars trial | 0 (single) + 6 (joint) + 0 (cost) = 6 |
| B3 ≥ baseline (226.79%) trial | 2 (single) + 3 (joint) + 0 (cost) = 5 |
| **B 全满足 trial** | **0 / 112** |
| max ret (TURBO=ON) | +290.58% (但 avg session 寿命 263.6 bars, B2 严重失败) |
| **结论** | **严证伪通过** |

### 联合结论

**两个标的 251 个 trial 全空间无 B 全满足点**. UVXY 4h 上 B1>0 与 B3≥baseline 互斥; VXX 4h 上 B3≥baseline 的 5 个 outlier 全部 B2 失败 (avg session 寿命 ≥ 236 bars, 远超 ≤ 20 bars 阈值, 物理上等于"长持仓", 与短线收割命题反向).

加上 Part 1B 给出的概念性必要条件 (i)-(iv) + Part 1C 给出的两个标的属性, 结论可外推到形态相近的"高 ADX 占比 + 中-高 ATR%" 类 ETF: 在本仓库设计的合规边界内, **战术化"短线收割"概念不能落地**.

---

## Part 5 — 后续行动 (将在 P9 执行)

证伪通过后, 处置方案 (B 路, 保留代码):

- `config.py`: `TURBO_ENABLED` 默认 OFF;
- `tactical_config.py`: 顶部 `EXPERIMENTAL` banner, 明示"本模块默认 OFF, 见 `reports/tactical_proof_of_impossibility.md`";
- `test.py`: 新增 4 个中性 regression test, 锁定战术化 4-action 的代码可达性 (`TURBO_ENABLED=1` 显式开启后仍可触发, 防止哑代码), 但不验证盈亏;
- 战术化全部代码保留, 不删除, 不重构. 若未来研究出更合适的标的 / 周期 / 资金量, 可重新打开 sweep 再做证伪测试;
- 不动 `CLAUDE.md` (用户明确要求).

---

## Part 6 — 已知边界与诚实声明

本证明的边界:

- 仅证明在 **{UVXY 4h, VXX 4h} × ≤5y × $10k cap** 内不可落地. VXX 数据窗口实际为 4y 11mo 21d, 略短于 5y, 但不影响 251 trial 全空间无 B 全满足点的结论.
- 不证明"战术化在更长周期 (10y) / 别的资产类 (单股 / 期货) / 更大资金量 ($100k+) 上也不能落地". Part 1B 提供概念性必要条件让结论可外推到形态相近 ETF, 但严格意义上**结论本身是局部的**, 不是全局的.
- 不做 git-bisect. `findings.md` F2 提到战术化期间 legacy 路径回归 -29.75pp 是独立 issue, 与本命题正交 — 本命题问"战术化能否落地", 不问"legacy 退化从哪个 commit 引入的".
- 本证明使用 backtest 撮合模型 (half-spread + 触价概率 + SEC/TAF), 已经覆盖回测/实盘的主要 gap, 但仍是 bar 级模型. 极端 tick / 队列级行为不在本证明覆盖范围内.

---

## 附录: 数据文件 + 字段说明

- `runtime/experiments/tactical_proof/uvxy_4h/single_dim.csv` — UVXY 单维 sweep, 50 trial
- `runtime/experiments/tactical_proof/uvxy_4h/joint.csv` — UVXY 联合 sweep, 81 trial
- `runtime/experiments/tactical_proof/uvxy_4h/cost.csv` — UVXY 成本敏感性, 8 trial (TURBO ON/OFF × 4 成本档)
- `runtime/experiments/tactical_proof/vxx_4h/single_dim.csv` — VXX 单维 sweep, 50 trial
- `runtime/experiments/tactical_proof/vxx_4h/joint.csv` — VXX 联合 sweep, 54 trial
- `runtime/experiments/tactical_proof/vxx_4h/cost.csv` — VXX 成本敏感性, 8 trial

每行字段:

- `trial_id` / `symbol` / `csv_path` / `interval` / `capital` / `env_overrides`
- `b1_trigger_total` = `b1_defensive + b1_forced + b1_profit_protect`
- `b2_avg_session_bars` = 平均 session 寿命 (4h bar 数)
- `b3_total_return_pct` = 5y 总回报 %
- `session_count` / `max_drawdown_pct` / `sharpe` (辅助指标, 不参与 B 判定)
