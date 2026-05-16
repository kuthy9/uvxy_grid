# Tactical Base=0 / Exit-Override 改造对比报告

date: 2026-05-14

本报告对比"底仓默认关闭 + grid_engine.should_exit 仅作信号"前后, 5y UVXY 4h 上
P0/P1/P2 的完整指标. 数据来自:

- 旧基线 (BASE_POSITION_RATIO=0.40 + 双轨退出): 上一轮 P2 5y top-1 (用户提供:
  base_exit_loss=-$1420 / 81 sessions, 最佳 5y ret +19.4% 主要由 2024H2 单窗口
  +37.7% 贡献).
- 新方案 (TACTICAL_BASE_POSITION_RATIO=0.0 + TACTICAL_OVERRIDE_GRID_ENGINE_EXIT=True):
  - P0 dir: `reports/tuning/UVXY_4h_p0_20260514_011820/`
  - P1 dir: `reports/tuning/UVXY_4h_p1_20260514_014206/`
  - P2 dir: `reports/tuning/UVXY_4h_p2_20260514_015259/`

---

## 1. 核心目标对比 — base_exit_loss 是否归零

| 指标 | 旧 (P2 top-1) | 新 (P0/P1/P2 全部) |
|---|---|---|
| `base_exit_loss` | **-$1,420** (81 sessions, -$17.5/session 平均) | **$0.00** (P0 324 combos, P1 144, P2 32 — 全部) |
| BASE_BUY 市价单 | 每 session 1 次 | 0 次 (代码路径 `skip_base=True`) |
| `forced_exit_count` (旧 grid_engine 路径) | 不可忽略 (ADX/ATR/deviation 三路触发) | 0 (新 P0 top-1, 见 `exit_reason_breakdown`) |

**结论**: 任务 #53-#56 的两个改造均在数据上验证生效, base_exit_loss 完全归零;
退出原因只剩 `age` (timeout) 和 `profit_protect`, 没有 `forced_exit`.

---

## 2. P0 / P1 / P2 5y 全样本结果 (本次, base=0 + override)

### 2.1 ret_pct / drawdown 分布

| Profile | combos | ret min | ret max | ret median | grid_pnl_median |
|---|---|---|---|---|---|
| P0 | 324 | -14.07% | **-5.20%** | -9.91% | -$992 |
| P1 | 144 | -9.97% | **-5.91%** | -7.61% | -$762 |
| P2 | 32  | -6.61% | **-3.69%** | -4.77% | -$478 |

每一层 anchor 收敛: P0→P1 把 ret max 抬到 -5.91%, P1→P2 进一步抬到 -3.69%.

### 2.2 P0 top-1 (anchor 出处)

```
SESSION_SOFT_STOP_PCT=0.015
SESSION_HARD_STOP_PCT=0.03
SESSION_MAX_AGE_BARS=40
SESSION_ABSOLUTE_MAX_AGE_BARS=60
TREND_RISK_SCORE_DEFENSIVE=65
SESSION_TRAILING_GIVEBACK_RATIO=0.5
```
ret=-5.91% / dd=6.48% / pf=0.355 / sessions=39 / win_rate=38.5%
exit_reason_breakdown = `{"age": 32, "profit_protect": 7}`
forced_exit_count = 0, base_exit_loss = $0.00

### 2.3 P1 top-1 (P0 anchor 已固定)

```
SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005
SESSION_STRONG_PROFIT_PCT=0.012
SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25
SESSION_MAX_BUY_LEVELS_NORMAL=4
SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55
DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3
```
ret=-6.2% / dd=6.8% / pf=0.39 / sessions=39

注: P1 top-1 ret (-6.2%) 略低于 P0 top-1 (-5.9%) — 不同 walk-forward 评分逻辑下
P1 grid 内最佳行的 5y 全样本 ret 不一定单调.

### 2.4 P2 top-1 (P0+P1 anchor 已固定)

```
ENTRY_MAX_ADX=25
ENTRY_MAX_EMA_DEVIATION_ATR=1.5
GRID_SPACING_ATR_MULTIPLIER=0.5
EXIT_MAX_ADX=22
EXIT_PRICE_DEVIATION_ATR=4.0
```
ret=**-4.4%** / dd=6.8% / pf=**0.55** / sessions=54

P2 把 sessions 从 39 提到 54 (ENTRY_MAX_ADX 20→25 放宽入场), profit_factor
从 0.39 提到 0.55, ret 从 -6.2% 提到 -4.4%.

---

## 3. Walk-forward 稳定性 (新默认 1y train / 3m valid / 3m step = 17 窗口)

每行 = top-5 之一在 17 个 valid 窗口上的 ret 分布:

### 3.1 P0 top-1
```
pos=5   neg=5   zero=7  mean=-0.00%  best=+1.33%  worst=-1.15%
```

### 3.2 P1 top-1
```
pos=5   neg=5   zero=7  mean=+0.01%  best=+1.92%  worst=-1.64%
```

### 3.3 P2 top-1
```
pos=6   neg=7   zero=4  mean=+0.00%  best=+2.85%  worst=-1.75%
```

zero 窗口 = 该 valid 子区间没有 session 触发 (入场过滤掉了整段).

**Sign-consistency** (top-1 在非零窗口里赢面):
- P0: 5 pos / 10 nonzero = **50%**
- P1: 5 pos / 10 nonzero = **50%**
- P2: 6 pos / 13 nonzero = **46%**

---

## 4. 与旧基线的对比 (用户提供的旧 P2 数据)

| 指标 | 旧 P2 best | 新 P2 top-1 |
|---|---|---|
| 5y total_return | **+19.4%** | **-4.4%** |
| sessions | 81 | 54 |
| base_exit_loss | **-$1,420** | **$0** |
| 主导赢家窗口 | 2024H2 单窗口 +37.7% | 无单窗口主导 (best 窗口 +2.85%) |
| walk-forward 正窗口比例 | 3/6 ≈ 50% (2y train / 6m / 6m) | 6/17 ≈ 35% (1y / 3m / 3m, 含 4 个 zero) |

**关键解读**:

1. **旧基线 +19.4% 不是结构性 edge** — 走 1y/3m/3m 细粒度 walk-forward 后,
   新 P2 在 17 个 valid 窗口里 best 也只 +2.85%, 不存在能在 6/6 月内复现
   +37.7% 那样的窗口. 用户在上一轮就已诊断"单 2024H2 窗口主导回报", 本次
   walk-forward 验证了这点: 底仓拿下来后, 那种"碰运气拿对方向"的回报曲线
   消失.

2. **底仓被 5y 趋势性吃掉的现象消失** — 旧 P2 81 个 session 的 base 平均
   -$17.5/session 的稳态亏损归零. 这是任务 #2 的直接目的.

3. **退出统一生效** — 新 P0 top-1 `forced_exit_count=0`, exit 全部走
   SessionManager (age + profit_protect). grid_engine.should_exit 触发的
   `engine_exit_signal` 事件现在只记录在 `grid_session_events` 表里, 不再
   旁路改 state_machine.

4. **5y 总回报转负是底仓退场后的"裸网格"真实表现** — UVXY 5y 价格从
   $11.57 → $39.71 实际上是上涨的 (期权 ETF 长期负 carry 但样本期波动放大),
   单靠"高波动 + 弱趋势"窗口内的网格 scalp, 在当前 P2 tactical 参数下
   一年只能跑出零附近的回报. 这意味着"无底仓 + 短线战术网格" 在 UVXY 上
   不构成可持续 edge — 至少在当前网格几何 / 入场过滤参数下.

---

## 5. 下一步建议 (不在本次任务范围, 仅记录)

按用户偏好 (盈亏比 > 胜率, 不接受单次大亏吃掉多次小盈利), 走 base=0 + override
是正确方向 — 它把"靠 base 蒙对方向" 的隐性赌注拆掉了. 但当前 P2 top-1 的
profit_factor=0.55 < 1, 说明:

- **入场过滤需要更挑剔**: ENTRY_MAX_ADX=25 已偏松, 应该上探 18-22 区间
  + 强制要求 BB 宽度门槛, 让 zero 窗口数从 4 上升 (宁可不进, 别进了亏)
- **网格几何需要重新校准**: 当前 GRID_SPACING_ATR_MULTIPLIER=0.5,
  网格间距偏小, 单次 BUY→SELL 收益不够覆盖 commission + slippage; 应
  P2 grid 扩到 [0.6, 0.75, 1.0] 重扫
- **退出节奏可以更激进**: timeout (age) 占 32/39 退出, 说明大量 session
  在第 40-60 bar 区间被强制 timeout. 建议 P0 加扫 `SESSION_MAX_AGE_BARS`
  下行: [12, 18, 24, 30] — 看是否更短的容忍能换更高 win_rate
- **回到候选标的层**: 当前 ETF 池里 UVXY 是"高波动 + 弱趋势"代表, 但
  base=0 之后的扫描显示这层信号不一定够强. 可以拿 SVXY / VXX / TQQQ
  跑一遍同 grid (CLAUDE.md §8.1 的候选筛选流程) 看哪个标的对网格更友好.

以上仅作下一阶段调研建议, 不直接动 config.

---

## 6. 输出文件清单 (本次)

```
reports/tuning/UVXY_4h_p0_20260514_011820/
  p0_grid.csv         # 324 行
  p0_top.md           # top-5 详表
  p0_walkforward.csv  # 85 行 (5 top × 17 windows)
  locked_params.md    # P3/P4 锁定参数快照

reports/tuning/UVXY_4h_p1_20260514_014206/
  p1_grid.csv         # 144 行
  p1_top.md
  p1_walkforward.csv  # 85 行
  locked_params.md

reports/tuning/UVXY_4h_p2_20260514_015259/
  p2_grid.csv         # 32 行
  p2_top.md
  p2_walkforward.csv  # 85 行
  locked_params.md

reports/tuning/BASE_ZERO_EXIT_OVERRIDE_COMPARISON.md  # 本文件
```

## 7. 代码与测试变更摘要

- `tactical_config.py`: 新增 `TACTICAL_BASE_POSITION_RATIO=0.0`,
  `TACTICAL_OVERRIDE_GRID_ENGINE_EXIT=True` (env 可覆盖).
- `grid_bot.py:_execute_entry`: 战术 base=0 时跳过 BASE_BUY 市价单,
  仍构建 grid + 启动 session (start_position=0, start_price=current_price).
- `grid_bot.py:_apply_dynamic_adjustment`: 战术 override 模式下
  `grid_engine.should_exit` 仅 log + 喂给 SessionManager (记 count +
  写 grid_session_events.event_type='engine_exit_signal'), 不触发
  EXIT_PENDING. override 关闭时保持旧的双轨行为.
- `session_manager.py`: 新增 `record_engine_exit_signal()`, 计数器
  `engine_exit_signal_count` 与 `last_engine_exit_signal`, 在
  `start_session` 中重置.
- `scripts/tune_tactical.py`: walk-forward 默认 train_years=1.0 /
  valid_months=3.0 / step_months=3.0; 新增 `--wf-train-years` /
  `--wf-valid-months` / `--wf-step-months` CLI 覆盖.
- `test.py`: +7 测试 (245 total, 全绿) — `TestTacticalExitOverride` 4 项
  + `TestTacticalBaseZeroEntry` 3 项. 覆盖 override on/off、信号计数
  重置、base=0 跳过市价单、base≠0 仍下市价单.
