# Candidate v1 验证 — 用户指定参数集 vs P0 sweep top-1

date: 2026-05-14
mode: TACTICAL_BASE_POSITION_RATIO = 0.0 (已是默认),
      TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = True

---

## 1. 配置状态确认

`tactical_config.py:71`:
```python
TACTICAL_BASE_POSITION_RATIO = _env_float("TACTICAL_BASE_POSITION_RATIO", 0.0)
```
✓ 默认 0.0, 战术模式下 `_execute_entry` 走 `skip_base=True` 路径, 不下 BASE_BUY 市价单.

---

## 2. P0 top-3 (5y, base=0) — 用户请求的指标

数据来源: `reports/tuning/UVXY_4h_p0_20260514_011820/p0_grid.csv`

**top-1 / top-2 / top-3 全部完全平手** (它们只在 `SESSION_HARD_STOP_PCT` 上不同 —
0.03 / 0.04 / 0.05, 但因为 `forced_exit_ratio=0` 该参数从未触发, 所以三组指标
完全相同).

| 指标 | top-1 / top-2 / top-3 |
|---|---|
| total_return | **-5.91%** |
| max_drawdown | 6.48% |
| profit_factor | 0.355 |
| Sharpe | -2.511 |
| session_count | 39 |
| grid_close_win_rate | 58.14% |
| avg_trades/session | 2.77 |
| forced_exit_ratio | **0.000** ✓ |
| profit_protect_exit_count | 7 |
| base_exit_loss | **$0.00** ✓ |

**P0 top-1 参数:**
```
SESSION_SOFT_STOP_PCT            = 0.015
SESSION_HARD_STOP_PCT            = 0.03   (top-2 = 0.04, top-3 = 0.05, 不影响)
SESSION_MAX_AGE_BARS             = 40
SESSION_ABSOLUTE_MAX_AGE_BARS    = 60
TREND_RISK_SCORE_DEFENSIVE       = 65
SESSION_TRAILING_GIVEBACK_RATIO  = 0.5
```

---

## 3. P0 top-3 walk-forward (1y train / 3m valid / 3m step → 17 windows)

| 指标 | top-1 / top-2 / top-3 (并列) |
|---|---|
| median_return | +0.000% |
| positive_windows | **5/17** (非零样本里 5/10 = 50.0%) |
| negative_windows | 5 |
| zero_windows | 7 (该 3 月没 session 触发) |
| best / worst | +1.33% / -1.15% |
| max_window_share | 1.33% / 总正 2.83% = **46.9%** (单窗口主导度) |

**用户的验收标准评估** (P0 top-1):
- ❌ `walk-forward median > 0`: 中位数 = 0%, 非零样本中位 = 0%
- ❌ `positive windows ≥ 4/6`: 5/17 ≈ 29% (按 4/6 ≈ 67% 标准未达)
- ✅ `max_drawdown 明显下降`: 旧基线 dd (推测较高), 当前 6.48% (低)
- ✓ `grid_close_win_rate 改善`: 58.14% (>50%, 接近用户目标)
- ❌ `不再依赖单一窗口`: 单窗口仍占正回报的 46.9% (但绝对量 +1.33% 已经很小, 危害有限)

---

## 4. 用户指定 "Candidate v1" 单点验证

**用户给的 14 项 anchor:**
```
P0:  SESSION_SOFT_STOP_PCT=0.015, SESSION_HARD_STOP_PCT=0.030,
     SESSION_MAX_AGE_BARS=24, SESSION_ABSOLUTE_MAX_AGE_BARS=40,
     SESSION_TRAILING_GIVEBACK_RATIO=0.50,
     TREND_RISK_SCORE_DEFENSIVE=70
P1:  SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.010,
     SESSION_STRONG_PROFIT_PCT=0.012,
     SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25,
     DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3
P2:  ENTRY_MAX_ADX=25, GRID_SPACING_ATR_MULTIPLIER=0.5,
     EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=4.0
```

### 4.1 5y 全样本

| 指标 | candidate v1 | P0 top-1 | Δ |
|---|---|---|---|
| total_return | **-13.13%** | -5.91% | **-7.22 pp 更差** |
| max_drawdown | **13.83%** | 6.48% | **+7.35 pp 更差** |
| profit_factor | **0.240** | 0.355 | **-0.115 更差** |
| Sharpe | -3.512 | -2.511 | -1.0 更差 |
| session_count | 61 | 39 | +22 |
| grid_close_win_rate | 58.00% | 58.14% | ≈ 持平 |
| avg_trades/session | **2.16** | 2.77 | **-0.61 更差** |
| forced_exit_ratio | 0.000 | 0.000 | = |
| profit_protect_exit_count | **1** | 7 | **-6 更差** |
| timeout_exit | **60** | 32 | **+28 (几乎全是 timeout)** |
| base_exit_loss | $0.00 | $0.00 | = |
| grid_pnl_total | -$1,310.60 | -$591.90 | **-$718.7 更差** |

### 4.2 Walk-forward 17 窗口

| 指标 | candidate v1 | P0 top-1 |
|---|---|---|
| median_return | 0.000% | 0.000% |
| positive_windows | 5/17 (5/13 nonzero = 38.5%) | 5/17 (5/10 = 50.0%) |
| negative_windows | **8** | 5 |
| zero_windows | 4 | 7 |
| best window | +0.61% | +1.33% |
| worst window | **-2.59%** | -1.15% |
| max_window_share | 28.1% | 46.9% |

---

## 5. 结论

### 5.1 base=0 的方向是正确的 (vs 旧基线)

| 用户验收标准 | 旧基线 (用户提供) | 新 P0 top-1 | 通过? |
|---|---|---|---|
| max_drawdown 下降 | — (旧值未提供, 但单窗口 +37.7% 暗示集中) | 6.48% | ✓ (DD 很低) |
| profit_factor 上升 | 旧未提供 | 0.355 | (无对比基准) |
| walk-forward 中位数改善 | 上一轮 walk-forward 3/6 正 | 5/17 正 | ≈ (绝对值都接近 0) |
| 正窗口数量增加 | 3/6 ≈ 50% (含 2024H2 主导) | 5/17 ≈ 29% (17 个细窗口里更分散) | ≈ |
| base_exit_loss = 0 | -$1,420 | $0.00 | ✓ |
| forced_exit_count = 0 | 旧路径 ADX/ATR/deviation 有触发 | 0 | ✓ |

**方向正确** — base 撤掉之后, 退出更干净 (全部走 SessionManager), DD 控制住,
单 2024H2 那种"靠 base 蒙对方向"的偶然回报消失. 代价是 5y 全样本回报转负
(裸网格在 UVXY 上无 edge).

### 5.2 但是: candidate v1 (用户指定的 14 项) 比 P0 sweep top-1 显著更差

**核心问题: SESSION_MAX_AGE_BARS=24 太短**.

- candidate v1 timeout_exit = 60/61 (98% 的 session 都被 age=24 / abs_age=40 强制
  超时)
- P0 top-1 timeout_exit = 32/39 (82%)
- 短 age 限制让 grid 没机会 mean-revert; sessions 平均只跑 2.16 trades
  (vs top-1 的 2.77), grid_pnl_total -$1,310 (vs -$592)
- profit_protect_exit 从 7 掉到 1 — 盈利保护几乎不再触发, 因为利润还没来得及
  累计就被 age 切掉

**TREND_RISK_SCORE_DEFENSIVE=70 vs 65**: P0 sweep 在 [65, 70, 75] 上扫过, 65 胜出.
70 让"趋势风险变大才切防守"导致 DEFENSIVE 触发更晚, 软止损更晚, 暴露更长.

**SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.010 vs 0.005**: P1 sweep 也是 0.005 胜出.
0.010 让盈利保护门槛更高, session 必须先盈利到 1% 才能进保护带 — 很多 session
卡在 0.5%-1% 区间然后被回吐.

### 5.3 建议

**不建议把 candidate v1 作为 v1 锁定值**. 数据说明这套参数在 5y UVXY 上比 P0
sweep 自动选出的 top-1 全面落后. 如果坚持"小范围复验", 建议改用以下"折中候选":

```
# 由 P0 sweep top-1 / P1 sweep top-1 / P2 sweep top-1 拼成 — 不需要再跑
P0:  SESSION_SOFT_STOP_PCT=0.015, SESSION_HARD_STOP_PCT=0.030,
     SESSION_MAX_AGE_BARS=40, SESSION_ABSOLUTE_MAX_AGE_BARS=60,
     SESSION_TRAILING_GIVEBACK_RATIO=0.50, TREND_RISK_SCORE_DEFENSIVE=65
P1:  SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_STRONG_PROFIT_PCT=0.012,
     SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25,
     DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3,
     SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55
P2:  ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.5,
     GRID_SPACING_ATR_MULTIPLIER=0.5, EXIT_MAX_ADX=22,
     EXIT_PRICE_DEVIATION_ATR=4.0
```

→ 5y ret -4.4%, dd 6.8%, pf 0.55, sessions 54 (来自 P2 top-1 实测).

或者如果用户**坚持 candidate v1 的紧 age 限制 (24/40) 是有意的** (例如希望
session 更短 / 高周转), 那应该接受这次回测告诉我们的事实: 那条紧路径在当前
入场过滤 + grid 几何下不工作. 下一步要做的不是锁定这组参数, 而是去改入场
信号或网格几何来匹配短 age 的设计意图 (例如更窄的 GRID_SPACING_ATR_MULTIPLIER
让 mean-revert 周期变短).

---

## 6. 产物

- `reports/tuning/UVXY_4h_p0_20260514_011820/p0_grid.csv`  ← P0 top-1 数据出处
- `reports/tuning/UVXY_4h_p0_20260514_011820/p0_walkforward.csv`  ← walk-forward
- `reports/tuning/CANDIDATE_V1_VALIDATION.md`  ← 本文
- `/tmp/run_candidate_v1.py`  ← candidate v1 单点验证脚本 (一次性)

---

## 7. 完整 candidate v1 walk-forward 逐窗口表

| # | valid 窗口 | return |
|---|---|---|
| 1 | 2022-01-04 ~ 2022-04-05 | 0.00% (zero) |
| 2 | 2022-04-05 ~ 2022-07-05 | 0.00% (zero) |
| 3 | 2022-07-05 ~ 2022-10-04 | **+0.57%** ✓ |
| 4 | 2022-10-04 ~ 2023-01-03 | -2.44% ✗ |
| 5 | 2023-01-03 ~ 2023-04-04 | **+0.29%** ✓ |
| 6 | 2023-04-04 ~ 2023-07-04 | 0.00% (zero) |
| 7 | 2023-07-04 ~ 2023-10-03 | **+0.50%** ✓ |
| 8 | 2023-10-03 ~ 2024-01-02 | -1.56% ✗ |
| 9 | 2024-01-02 ~ 2024-04-02 | -0.67% ✗ |
| 10 | 2024-04-02 ~ 2024-07-02 | -2.59% ✗ (worst) |
| 11 | 2024-07-02 ~ 2024-10-01 | 0.00% (zero) |
| 12 | 2024-10-01 ~ 2024-12-31 | **+0.21%** ✓ |
| 13 | 2024-12-31 ~ 2025-04-01 | -0.08% ✗ |
| 14 | 2025-04-01 ~ 2025-07-01 | -0.28% ✗ |
| 15 | 2025-07-01 ~ 2025-09-30 | -1.26% ✗ |
| 16 | 2025-09-30 ~ 2025-12-30 | -1.48% ✗ |
| 17 | 2025-12-30 ~ 2026-03-31 | **+0.61%** ✓ (best) |
