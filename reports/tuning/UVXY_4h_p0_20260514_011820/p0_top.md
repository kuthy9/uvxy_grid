# Tactical Tune — Profile **p0**

- Symbol: `UVXY` | Interval: `4h` | Years: `5.0`
- Total combinations explored: **324**
- Rows passing filters (sessions ≥ 5, dd < 50%): **5**
- Generated at: 2026-05-14T01:40:20.907589

## Scoring

score = annual_ret + 0.5×sharpe + 1.5×(profit_factor−1) − drawdown − forced_exit_ratio
  - session_count < 20 折半 (鼓励样本量)

## Top Results

| Rank | score | total_return_pct | max_drawdown_pct | sharpe | profit_factor | session_count | session_win_rate | grid_close_win_rate | forced_exit_ratio | defensive_mode_ratio | SESSION_ABSOLUTE_MAX_AGE_BARS | SESSION_HARD_STOP_PCT | SESSION_MAX_AGE_BARS | SESSION_SOFT_STOP_PCT | SESSION_TRAILING_GIVEBACK_RATIO | TREND_RISK_SCORE_DEFENSIVE | average_session_pnl | avg_trades_per_session | diag_avg_realized_session | diag_avg_unrealized_session | diag_base_exit_loss | diag_buy_fills_per_session | diag_grid_close_count | diag_grid_pnl_total | diag_sell_fills_per_session | diag_sessions_with_grid_sell | exit_reason_breakdown | forced_exit_count | max_session_loss | profit_protect_exit | timeout_exit | total_grid_sessions | total_trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | -2.300 | -5.911 | 6.480 | -2.511 | 0.355 | 39 | 38.462 | 58.140 | 0.000 | 0.000 | 60 | 0.03 | 40 | 0.015 | 0.5 | 65 | -18.158638557463707 | 2.769230769230769 | 6.959602180286538 | -25.118240737750245 | 0.0 | 1.6666666666666667 | 15 | -591.9019775901191 | 1.1025641025641026 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 109 |
| 2 | -2.300 | -5.911 | 6.480 | -2.511 | 0.355 | 39 | 38.462 | 58.140 | 0.000 | 0.000 | 60 | 0.04 | 40 | 0.015 | 0.5 | 65 | -18.158638557463707 | 2.769230769230769 | 6.959602180286538 | -25.118240737750245 | 0.0 | 1.6666666666666667 | 15 | -591.9019775901191 | 1.1025641025641026 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 109 |
| 3 | -2.300 | -5.911 | 6.480 | -2.511 | 0.355 | 39 | 38.462 | 58.140 | 0.000 | 0.000 | 60 | 0.05 | 40 | 0.015 | 0.5 | 65 | -18.158638557463707 | 2.769230769230769 | 6.959602180286538 | -25.118240737750245 | 0.0 | 1.6666666666666667 | 15 | -591.9019775901191 | 1.1025641025641026 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 109 |
| 4 | -2.300 | -5.911 | 6.480 | -2.511 | 0.355 | 39 | 38.462 | 58.140 | 0.000 | 0.000 | 60 | 0.03 | 40 | 0.02 | 0.5 | 65 | -18.158638557463707 | 2.769230769230769 | 6.959602180286538 | -25.118240737750245 | 0.0 | 1.6666666666666667 | 15 | -591.9019775901191 | 1.1025641025641026 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 109 |
| 5 | -2.300 | -5.911 | 6.480 | -2.511 | 0.355 | 39 | 38.462 | 58.140 | 0.000 | 0.000 | 60 | 0.04 | 40 | 0.02 | 0.5 | 65 | -18.158638557463707 | 2.769230769230769 | 6.959602180286538 | -25.118240737750245 | 0.0 | 1.6666666666666667 | 15 | -591.9019775901191 | 1.1025641025641026 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 109 |

## Per-top 诊断

解读:
- **exit_reason 分布**: session 退出原因占比. hard_stop / trend_risk / age 是主要 forced_exit 来源.
- **buy/sell fills/session**: 每 session 平均成交笔数. 太低 (<2) 说明 session 还没充分跑.
- **grid_close_count**: 网格平仓数 (GRID_SELL 配对). 0 说明 sell trigger 从未命中.
- **base_exit_loss vs grid_pnl**: 底仓退出的现金差 vs 网格交易盈亏. 哪一方主导亏损?
- **avg_realized vs avg_unrealized**: session 已实现 vs 浮盈差距, 揭示 'EXIT 时机' 问题.

### Top 1
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_SOFT_STOP_PCT=0.015, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.158638557463707, avg_trades_per_session=2.769230769230769, diag_avg_realized_session=6.959602180286538, diag_avg_unrealized_session=-25.118240737750245, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.6666666666666667, diag_grid_close_count=15, diag_grid_pnl_total=-591.9019775901191, diag_sell_fills_per_session=1.1025641025641026, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=109`
- score=-2.300 | total_return=-5.91% | dd=6.48% | sharpe=-2.511
- sessions=39 | session_win_rate=38.5% | profit_factor=0.35
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.67 | **sell_fills/session**=1.10
- **grid_close_count**=15 (sessions with grid_sell=10) | grid_close_win_rate=58.1%
- grid_pnl_total=$-591.90 | base_exit_loss=$+0.00
- avg_realized/session=$+6.96 | avg_unrealized/session=$-25.12

### Top 2
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.04, SESSION_MAX_AGE_BARS=40, SESSION_SOFT_STOP_PCT=0.015, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.158638557463707, avg_trades_per_session=2.769230769230769, diag_avg_realized_session=6.959602180286538, diag_avg_unrealized_session=-25.118240737750245, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.6666666666666667, diag_grid_close_count=15, diag_grid_pnl_total=-591.9019775901191, diag_sell_fills_per_session=1.1025641025641026, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=109`
- score=-2.300 | total_return=-5.91% | dd=6.48% | sharpe=-2.511
- sessions=39 | session_win_rate=38.5% | profit_factor=0.35
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.67 | **sell_fills/session**=1.10
- **grid_close_count**=15 (sessions with grid_sell=10) | grid_close_win_rate=58.1%
- grid_pnl_total=$-591.90 | base_exit_loss=$+0.00
- avg_realized/session=$+6.96 | avg_unrealized/session=$-25.12

### Top 3
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.05, SESSION_MAX_AGE_BARS=40, SESSION_SOFT_STOP_PCT=0.015, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.158638557463707, avg_trades_per_session=2.769230769230769, diag_avg_realized_session=6.959602180286538, diag_avg_unrealized_session=-25.118240737750245, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.6666666666666667, diag_grid_close_count=15, diag_grid_pnl_total=-591.9019775901191, diag_sell_fills_per_session=1.1025641025641026, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=109`
- score=-2.300 | total_return=-5.91% | dd=6.48% | sharpe=-2.511
- sessions=39 | session_win_rate=38.5% | profit_factor=0.35
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.67 | **sell_fills/session**=1.10
- **grid_close_count**=15 (sessions with grid_sell=10) | grid_close_win_rate=58.1%
- grid_pnl_total=$-591.90 | base_exit_loss=$+0.00
- avg_realized/session=$+6.96 | avg_unrealized/session=$-25.12

### Top 4
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_SOFT_STOP_PCT=0.02, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.158638557463707, avg_trades_per_session=2.769230769230769, diag_avg_realized_session=6.959602180286538, diag_avg_unrealized_session=-25.118240737750245, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.6666666666666667, diag_grid_close_count=15, diag_grid_pnl_total=-591.9019775901191, diag_sell_fills_per_session=1.1025641025641026, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=109`
- score=-2.300 | total_return=-5.91% | dd=6.48% | sharpe=-2.511
- sessions=39 | session_win_rate=38.5% | profit_factor=0.35
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.67 | **sell_fills/session**=1.10
- **grid_close_count**=15 (sessions with grid_sell=10) | grid_close_win_rate=58.1%
- grid_pnl_total=$-591.90 | base_exit_loss=$+0.00
- avg_realized/session=$+6.96 | avg_unrealized/session=$-25.12

### Top 5
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.04, SESSION_MAX_AGE_BARS=40, SESSION_SOFT_STOP_PCT=0.02, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.158638557463707, avg_trades_per_session=2.769230769230769, diag_avg_realized_session=6.959602180286538, diag_avg_unrealized_session=-25.118240737750245, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.6666666666666667, diag_grid_close_count=15, diag_grid_pnl_total=-591.9019775901191, diag_sell_fills_per_session=1.1025641025641026, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=109`
- score=-2.300 | total_return=-5.91% | dd=6.48% | sharpe=-2.511
- sessions=39 | session_win_rate=38.5% | profit_factor=0.35
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.67 | **sell_fills/session**=1.10
- **grid_close_count**=15 (sessions with grid_sell=10) | grid_close_win_rate=58.1%
- grid_pnl_total=$-591.90 | base_exit_loss=$+0.00
- avg_realized/session=$+6.96 | avg_unrealized/session=$-25.12


## 跨 top 整体诊断

整个 top 集合的 exit_reason 主导分布:
- `age`: 160 (82.1%)
- `profit_protect`: 35 (17.9%)

### 自动建议

- ℹ️ age 退出占比偏高: 考虑加大 SESSION_ABSOLUTE_MAX_AGE_BARS.
- ℹ️ avg_unrealized << avg_realized: session EXIT 时仍带较大浮亏, 可能 SOFT/HARD 触发节奏太早, 没等到反弹.

## 下一步建议 (固定流程)

1. 不要直接把 top-1 参数写回 config.py — 先 walk-forward 验证 (--walk-forward)
2. P0 跑完后, 在 top-1 附近跑 P1 (--anchor-params JSON of top-1 P0 params)
3. P0+P1 top 3 邻域跑 P2 light
4. 检查每行 forced_exit_ratio — 过高 (>50%) 说明参数仍偏紧
5. profit_factor < 1.0 即使 total_return 为正, 也说明仰仗少数大盈利, 不稳健