# 战术化网格不可落地性证明 — Design Spec

> **日期**: 2026-05-14
> **状态**: design (待用户 review)
> **负责人**: krisjiang (用户) + Claude
> **关联文件**: `findings.md` / `task_plan.md` / `progress.md` / `runtime/experiments/tactical_proof/` (将创建)

---

## 1. 背景与动机

仓库已实现"战术化网格" (Aggressive Tactical Session Grid):
- `tactical_config.py` / `tactical_rules.py` / `session_manager.py`
- `grid_bot.py` 战术分支 8 处
- `state_machine.py` 6 状态扩展 (OFFENSIVE/DEFENSIVE/COOLDOWN/EXIT_PENDING)
- `scripts/tune_tactical.py` 分层调参

设计意图: **极大增强短线收割** (相对 V49 长 hold 网格).

上一轮调查 (`findings.md`) 实测: 战术化在 UVXY 4h 上**稳定吃 -9pp**,
且 `tactical_config.py` 多处阈值已被改成"永不触发" (999/99999/1.0/0.0),
源码注释自证 "启用就让 ret 变差". 用户多轮调参均失败.

但用户不接受 "战术化在 UVXY 上输给 V49" 作为可接受结论, 提出两层质疑:
1. UVXY 不适配 ≠ 战术化概念不可行
2. 上轮证据只在 UVXY 4h, 不能推广

本 spec 通过 **理论 + 实测严证伪** 回答: 战术化网格"短线收割"概念是否可落地.

---

## 2. 命题

**严证伪命题**:

> 在 (UVXY 4h 5y, VXX 4h 5y) × $10,000 cap × `SimulatedExecutor` 真实化撮合
> 边界内, **不存在**任何战术化配置同时满足以下三条 (B 标准):
>
> - **B1**: Defensive / Forced_exit / Profit_protect 触发次数 > 0
> - **B2**: 平均 session 寿命 ≤ 20 bars (V49 baseline ~60 bars 的 1/3, 即"真的短线")
> - **B3**: 5y 回报 ≥ 各标的 TURBO=OFF baseline
>   - UVXY 4h baseline (上轮实测, $2k cap): +80.16%. 本轮将用 $10k cap 重测.
>   - VXX 4h baseline: 待跑出.

**判定**:
- 严证伪通过 (两个标的全 sweep 空间都无满足 B 的点) → 战术化概念在合规边界内**不能落地**
- 严证伪失败 (任一标的找到反例) → 命题反转, 反例参数集报告给用户决策

**对照基线 V49 worktree (+109.91%)**: 仅作参考, 不作为 B3 阈值. B3 用同一 HEAD 下
TURBO=OFF 跑出的回报, 排除"战术化期间 legacy 路径回归" (-29pp) 这个独立问题.

---

## 3. 证明结构

### Part 0 — 数据 + 资金口径准备

- `data/vxx.py`: 复用 `data/qqq.py` 的 Alpaca pipeline, 拉 VXX 4h **前 5 年**数据
  (从今日往前 5 年). 若 Alpaca 限制拉不满 5 年, 取最长 + 在 report 中标注实际窗口.
  落 `data/vxx_4h.csv`, 字段 / 时区 / 时间格点与 `data/uvxy_4h.csv` 对齐 (DataProvider
  能直接消费).
- `backtest.py`: `BACKTEST_DEFAULT_CAPITAL` 2000 → **10000**.
- `scripts/prove_tactical.py` 与 sweep 内部所有 backtest 调用一律显式 `--capital 10000`,
  不依赖默认值 (CLAUDE §1 反硬编码规则).
- `scripts/tune.py` / `scripts/tune_tactical.py` 当前是否硬编码 capital — 在
  implementation 阶段检查, 若是改为 argparse 显式; 不在本 spec 范围内做更深动作.

### Part 1 — 机制 (理论)

写到 `reports/tactical_proof_of_impossibility.md` Part 1 节, **~1200 字**, 中文.

**1A. UVXY 专属冲突 (沿用上轮分析)**:
- UVXY 5y 真实 edge (V49 worktree 实证): 长 hold (~60 bars) + ADX > 22 时
  `grid_engine.should_exit` 在 trend 高位 EXIT_GRID (13 笔此类 exit 累计 +$500,
  +109% 主要来源).
- 战术化 4 个动作每个都与该 edge 结构性冲突 — 列表逐项:
  - DEFENSIVE 进入 → 关闭 BUY 一侧, 杀掉 fade pullback 路径
  - FORCE_EXIT 提前 → 卖在 trend 中段而非高位
  - PROFIT_PROTECT trailing giveback → 永远早卖
  - PARTIAL_EXIT 减仓 → 后续反弹空间被砍

**1B. 战术化概念 (Symbol-agnostic) 必要条件**:

战术化要在任何标的上"短线收割"赢, 需要标的同时满足:

- **(i) 短周期 ranging window 密度**: 标的存在大量 ≤20 bars 的可识别 ranging 段
  (≥ 数十段 / 年), 战术化才有"短线"对象可收.
- **(ii) 等待型 edge**: ranging 段之间的趋势期足够长 + 可识别, 让 DEFENSIVE / FORCE_EXIT
  真的能省下亏损. 否则 "切到 DEFENSIVE 然后趋势没来 / 来了又走" 是负贡献.
- **(iii) 成本占比基线低**: cost (commission + slippage + 监管费) 占毛利比例足够低
  让短线频繁交易能赚钱. 由于战术化目标是短线 session (B2 阈值 ≤20 bars), 平均
  trades/session 必然显著高于长 hold baseline, cost 暴露随之线性放大; 起点 cost 占比
  必须低 (具体阈值在 Part 1C 表格量化后给出).
- **(iv) 成本敏感性低**: 单位 spread / commission 上下 ±50% 时, 战术化最优回报衰减
  应 ≤ baseline 的衰减. 否则战术化是 "对成本假设敏感的 fragile alpha", 实盘成本若
  比回测略高一点就归零. 在 report 中量化: 给定最优战术化参数, 把 `BT_MARKET_SLIP_BPS`
  从 5 → 7.5 (50% worse), `IBKR_COMMISSION_PER_SHARE` 从 0.0035 → 0.00525, 看回报衰减.

**1C. UVXY / VXX 在 (i)/(ii)/(iii)/(iv) 上的属性对比**:
列一张表, 用 V49 worktree + 本轮 sweep 的数据填空. 表内 "(待统计)" / "(待跑)"
项在 implementation 阶段从历史 CSV + baseline backtest 中量化填入, 不影响本 design
的决策框架 (design 只要求"对比维度"完整, 不要求 design 阶段就有数值).
| 维度 | UVXY 4h | VXX 4h |
|---|---|---|
| ATR% 中位数 | ~3% (高) | ~2% (中高) |
| ADX 长期均值 | (待统计) | (待统计) |
| 可识别 ≤20bar ranging 段数 / 年 | (待统计) | (待统计) |
| 趋势期典型长度 | (待统计) | (待统计) |
| 日均成交额 USD | ~$1B | ~$1B |
| spread bps | 5-10 | 5-10 |
| cost / 毛利 (V49 OFF baseline, 10k cap) | (待跑) | (待跑) |
| 1× vs 1.5× 杠杆 → 长 hold edge 强度 | 强 (V49) | 弱 (1× 振幅小, 不积累深 unrealized loss) |

**预测**: VXX 在 (i)/(ii) 上比 UVXY 更有利 (1× 振幅更稳, ranging/trend 切换更清晰),
(iii)/(iv) 接近. 若战术化连 VXX 也满足不了 B, 严证伪结论站得住.

### Part 2 — 单维 sweep

对 10 个旋钮各跑独立 sweep, 其他参数保持默认 (战术化中性化默认). 每个旋钮报告:
B1/B2/B3 触发情况, 最优点, 与 baseline 差距.

**旋钮清单**:
```python
SESSION_HARD_STOP_PCT             ∈ [0.02, 0.05, 0.10, 0.15, 0.20, 1.0]
SESSION_SOFT_STOP_PCT             ∈ [0.01, 0.03, 0.05, 0.10, 0.20, 1.0]
SESSION_MAX_AGE_BARS              ∈ [6, 12, 20, 30, 60, 99999]
TREND_RISK_SCORE_DEFENSIVE        ∈ [40, 50, 60, 70, 80, 999]
TREND_RISK_SCORE_FORCE_EXIT       ∈ [60, 70, 80, 90, 999]
SESSION_MIN_PROFIT_TO_PROTECT_PCT ∈ [0.01, 0.02, 0.03, 0.05, 1.0]
SESSION_TRAILING_GIVEBACK_RATIO   ∈ [0.30, 0.50, 0.70, 1.0]
TACTICAL_OVERRIDE_GRID_ENGINE_EXIT∈ [False, True]
TACTICAL_MAX_BUY_DEPTH_ATR        ∈ [0, 1.0, 1.5, 2.0, 3.0]
SESSION_NO_FILL_TIMEOUT_BARS      ∈ [0, 12, 24, 48, 96]
```
合计 ~54 点 × 2 标的 = **~108 backtest**.

**成本敏感性 sweep (Part 2 子节)**:
基线参数 = 战术化默认. 扫:
```
(BT_MARKET_SLIP_BPS, IBKR_COMMISSION_PER_SHARE) ∈
  [(5.0, 0.0035), (7.5, 0.00525), (10.0, 0.007), (3.0, 0.00175)]
```
4 点 × 2 标的 = 8 backtest. 输出: 战术化默认回报相对于成本变化的弹性曲线, 与 TURBO=OFF
同条件对比. 这一项验证 1B(iv) 必要条件 ("成本敏感性低").

### Part 3 — 联合 sweep

**旋钮选择算法 (确定性, 不是 ad-hoc)**:

1. Part 2 单维 sweep 跑完后, 对每个旋钮 × 每个标的 (10 × 2 = 20 个 cell), 找该旋钮
   sweep 中"最接近满足 B 的点": 定义 score = (#B 满足条数) + (B3 相对 baseline 的差距
   归一化到 [0, 1]).
2. 把 10 个旋钮按 score 排序, 取**前 4 名作为 Part 3 联合 sweep 的轴**.
3. **Fallback**: 若前 4 名中有旋钮的 B 条数全为 0 (= 任意点都没满足任何 B 条),
   说明该旋钮没贡献, 用预设清单替补: `SESSION_MAX_AGE_BARS`,
   `TACTICAL_OVERRIDE_GRID_ENGINE_EXIT`, `TREND_RISK_SCORE_DEFENSIVE`, `SESSION_HARD_STOP_PCT`.
   这 4 个是机制层面最有可能"组合突破"的轴 (覆盖了 session 寿命 / V49 edge 重叠 /
   defensive 触发 / hard stop).

这样 Part 3 的轴选择由 Part 2 数据驱动, 不再是设计者拍脑袋.

每轴 3-5 点, 总组合 ≤ 150 × 2 标的 = **~300 backtest**. 7-worker 并行 (`scripts/prove_tactical.py
--workers 7`), 估计 ~30-60 分钟.

输出: 完整 Pareto 前沿 (B1 trigger count, B2 avg age, B3 return) 三维数据表, CSV 落
`runtime/experiments/tactical_proof/{symbol}/joint_sweep.csv`.

### Part 4 — 结论裁决

对每个标的 (UVXY, VXX) 分别裁决:
1. Part 2/3 全 sweep 空间是否存在满足 B 三条的点
2. 若存在, 列出反例参数 + 关键指标
3. 若不存在, 量化 "离 B 最近的点" — 例如 "B1/B2 满足但 B3 = +63%, 距 baseline 80%
   差 17pp", 给出该点的参数

**联合判定**:
- 两个标的都无满足点 → **严证伪通过**
- UVXY 无 VXX 有 → 战术化概念能落地, 仅 UVXY 不适配, 需要扩展 per-symbol config
- 两个都有 → 命题反转, sweep 结果是反例参数集
- VXX 无 UVXY 有 → 实验设计或代码有问题, 回头查 (不应出现)

### Part 5 — 失败处理 (条件 deliverable)

**if 严证伪通过**:
- `config.TURBO_ENABLED` 默认 `False`
- `tactical_config.py` 顶部加 banner:
  ```
  # ⚠️ EXPERIMENTAL — 2026-05-14 严证伪通过, 战术化在 UVXY 4h + VXX 4h 5y 上
  # 不能同时满足 (Defensive 触发 > 0, 平均 session 寿命 ≤ 20bars, 回报 ≥ TURBO=OFF baseline).
  # 默认 OFF. 完整证据见 reports/tactical_proof_of_impossibility.md
  ```
- **不删** tactical_* / session_manager / state_machine 6 状态扩展 (代码全部保留)
- `test.py` 新增中性 regression test (见 §4.4)

**if 严证伪失败 (找到反例)**:
- 不擅自动 `config.py`, 不擅自改 `tactical_config.py` 默认值
- 把反例参数集 + Pareto 数据交给用户决策是否合入默认

---

## 4. 并行修复 (F5 / F4)

### 4.1 F5 — `entry_filter.py` 移除 `datetime.now()` fallback

**位置**: `entry_filter.py` L85-103 (`evaluate`), L116-129 (`evaluate_precomputed`),
具体 fallback 在 L98 / L124 / L136.

**修改**:
- 签名: `evaluation_time: Optional[datetime] = None` → `evaluation_time: datetime` (必填)
- 删除 `or datetime.now()` fallback
- `import` 处保留 `datetime`, 但不再被 fallback 调用

**验证**: 跑 `python test.py` 全套. 现有 248 个测试中调用 `EntryFilter.evaluate*` 的
用例必须全部已传 `evaluation_time` (回测从 `HistoricalClock` 取, 实盘从 `clock.now()` 取);
若有未传的, 在 implementation 阶段定位并修.

**时机**: 在证明开始前修, 与下一项一起.

### 4.2 F4a — README §3.2 (4 状态 → 6 状态)

`README.md` §3.2 当前文字:
> 四状态 FSM (`SCANNING → WAITING_ENTRY → ACTIVE_GRID → EXIT_PENDING`)

改为:
> 六状态 FSM (`SCANNING → WAITING_ENTRY → OFFENSIVE_GRID → DEFENSIVE_GRID → EXIT_PENDING → COOLDOWN`)

**不动 CLAUDE.md** (用户明确要求).

**时机**: 与 F5 一起, 证明开始前.

### 4.3 F4b — README §5 数据 + 战术化证明结论

§5 当前数据是上轮 $2000 cap 下的实测 + 已经过时的 V49 引用. 等本轮证明跑完, 一次性
更新为:
- 用 $10,000 cap 重测 V49 baseline + TURBO=ON/OFF (注: V49 用 git worktree, 不在 HEAD 改)
- 加入证明结论 + 链接 `reports/tactical_proof_of_impossibility.md`

**时机**: 证明完成后, 与 deliverable 一并 commit.

### 4.4 中性 regression test (条件: 严证伪通过)

不锁定 "ON 比 OFF 差" 政治化结论. 改为锁定 "**战术化 4 个动作的代码路径不是 dead code**":

```python
class TestTacticalActionsReachable(unittest.TestCase):
    """战术化代码可达性 (中性 regression).

    目的: 防止战术化分支无声退化为 dead code. 不断言 "ON vs OFF" 优劣,
    只断言 4 个 action 各自在某种合成行情下能被触发, 即代码路径活着.
    未来用户若决定调整战术化阈值, 这个 test 仍有意义.
    """

    def test_defensive_triggers_on_strong_downtrend(self):
        # 合成: ADX=35 + EMA 下行 + 连续 5 bar 下跌
        # 用 TREND_RISK_SCORE_DEFENSIVE=50 (非默认 999) 跑 evaluate_session
        # 断言 evaluation.action == ACTION_ENTER_DEFENSIVE

    def test_force_exit_triggers_on_atr_explosion(self):
        # 合成: ATR% = 12% (远超 SESSION_ATR_PCT_EXPLOSION_STOP=8%)
        # 断言 evaluation.action == ACTION_FORCE_EXIT

    def test_profit_protect_triggers_on_trailing_giveback(self):
        # 合成: peak_pnl=$200, total_pnl=$80, giveback=60%
        # 用 SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, TRAILING_GIVEBACK_RATIO=0.5
        # 断言 evaluation.action == ACTION_PROFIT_PROTECT_EXIT

    def test_partial_exit_triggers_on_strong_profit(self):
        # 合成: pnl_pct=5%, position_value=$500
        # 用 SESSION_STRONG_PROFIT_PCT=0.03, PARTIAL_EXIT_RATIO=0.5
        # 断言 evaluation.action == ACTION_PARTIAL_PROFIT_EXIT
```

这 4 个测试用合成 market context + 临时 tactical_config 阈值 (不依赖 module-level
默认值), 保证战术化的 4 个 action 都能在受控输入下触发. **它们不锁定"战术化应该开"**
也不锁定"战术化应该关", 只锁定"代码能跑". 即使将来用户改造战术化或重启实盘, 这套
测试都不会过时.

**时机**: 与 §5 README 更新一起, 严证伪通过分支才加.

---

## 5. Deliverables

**不论证伪通过 / 失败**:
- `data/vxx.py` (新, Alpaca 拉数脚本)
- `data/vxx_4h.csv` (新, 前 5 年 VXX 4h 数据)
- `scripts/prove_tactical.py` (新, sweep 驱动, argparse: `--symbol`, `--csv`,
  `--interval`, `--capital`, `--workers`, `--part {2,3,cost}`)
- `runtime/experiments/tactical_proof/{uvxy_4h, vxx_4h}/{single_dim, joint, cost}.csv`
- `reports/tactical_proof_of_impossibility.md` (中文; 含 Part 1 机制 + Part 2/3 数据
  + Part 4 结论; 即使证伪失败也是"探索报告")
- `findings.md` 终态 (含证明结论简版)
- `entry_filter.py` (F5)
- `backtest.py` (capital 默认 10000)
- `README.md` §3.2 (F4a) + §5 (F4b)

**仅证伪通过附加**:
- `config.py` `TURBO_ENABLED` 默认 `False`
- `tactical_config.py` 顶部 EXPERIMENTAL banner
- `test.py` 新增 `TestTacticalActionsReachable` (4 个用例)

---

## 6. 不在范围内 (诚实声明)

- 不做 git-bisect 定位"战术化期间 legacy 路径吃掉 -29pp"问题. 那是独立 issue, 与本命题正交.
- 不证明"战术化在更长周期 (1m/15m) 或别的资产类别 (股票 / 期货) 上也不能落地".
  Part 1B 给出概念性必要条件, 让结论在论证层可推广; 实测仅覆盖 4h × UVXY/VXX.
- 不动 `CLAUDE.md` (用户明确要求, 即使其 §1 状态机描述过时也不在本轮修).
- 不改 `PROJECT_STATUS.md` (hook 自动维护).
- 不动其他被战术化期间改过的 legacy 路径 (state_machine 6 状态扩展 / risk_manager
  account_risk 接口 / WAITING_ENTRY 解耦 etc.) 在本轮中**只读不改**.
- 不动 `scripts/tune.py` / `scripts/tune_tactical.py` 的核心逻辑. 本轮只用
  `scripts/prove_tactical.py` 这个新脚本跑 sweep.

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Alpaca 拉不到 VXX 完整 5y | Part 1C 数据不全 | 用能拉到的最长窗口, report 标注 |
| sweep 跑出来某点满足 B 但是 walk-forward 一过就破 | 反例参数实战不稳 | sweep 输出额外标注 in-sample 性能 + 在 report 中加 caveat: "in-sample 反例 ≠ 可上线"|
| `prove_tactical.py` 与现有 `tune.py` 重复 | 代码重复 | 设计上 `prove_tactical.py` 只跑 sweep 不做 walk-forward / OOS, 把 tune.py 当库调用 (复用 SimulatedExecutor + GridBot 装配); 不重写 |
| 严证伪失败 (B 反例存在) 让用户改 config 默认 | 实盘上线风险 | 反例不擅自合入. 用户决策后, 必须经 `tune_tactical.py` 的 walk-forward + OOS 验证才能进默认 |
| 中性 regression test 误伤 | 阻塞未来重构 | 4 个 test 用合成 context, 不依赖 tactical_config 默认值. 重构 tactical_rules 内部不会破; 只有真删 action 才破 |
| F5 改完发现某调用方未传 evaluation_time | test.py 红色 | 在 implementation 阶段先 grep 所有调用方, 补传; 若有需要 wall-clock 的实盘路径, 在调用方显式 `clock.now()` |

---

## 8. 验收 (我什么时候可以说"做完了")

1. `data/vxx_4h.csv` 落盘, 数据连续 (无空洞), 列与 `data/uvxy_4h.csv` 同 schema
2. `scripts/prove_tactical.py` 跑通 (`--part 2` / `--part 3` / `--part cost` 各能独立跑)
3. `runtime/experiments/tactical_proof/{uvxy_4h, vxx_4h}/` 三类 CSV 完整
4. `reports/tactical_proof_of_impossibility.md` 含 Part 1-4 全部章节, 数据表填满
5. `findings.md` 更新 (含证明结论简版)
6. `entry_filter.py` 不再有 `datetime.now()` fallback
7. `backtest.py` `BACKTEST_DEFAULT_CAPITAL = 10000`
8. `README.md` §3.2 6 状态 + §5 新数据 + 证明链接
9. `python test.py` 全绿 (含可能新增的 `TestTacticalActionsReachable`)
10. 严证伪通过时, `config.TURBO_ENABLED` 默认 `False` + `tactical_config.py` banner 在位

---

## 9. 工作量估算

| 阶段 | 时间 |
|---|---|
| F5 + F4a (entry_filter / backtest default / README §3.2) | ~15 min |
| `data/vxx.py` + 拉数 | ~30 min (含 Alpaca rate limit) |
| `scripts/prove_tactical.py` (单维 + 联合 + 成本 sweep, 复用 backtest 装配) | ~60 min 写 + 调试 |
| sweep 计算 (~400 backtest, 7 worker) | ~60-90 min |
| 写 `reports/tactical_proof_of_impossibility.md` | ~60 min |
| F4b (README §5 + findings.md 更新) | ~15 min |
| 严证伪通过时: TURBO=False + banner + 4 个 regression test | ~30 min |
| **总计** | **~4-5 小时** (主要瓶颈是 sweep 计算) |
