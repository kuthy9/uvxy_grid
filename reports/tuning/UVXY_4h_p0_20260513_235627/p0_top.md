# Tactical Tune — Profile **p0**

- Symbol: `UVXY` | Interval: `4h` | Years: `5.0`
- Total combinations explored: **324**
- Rows passing filters (sessions ≥ 5, dd < 50%): **5**
- Generated at: 2026-05-14T00:06:23.609238

## Scoring

score = annual_ret + 0.5×sharpe + 1.5×(profit_factor−1) − drawdown − forced_exit_ratio
  - session_count < 20 折半 (鼓励样本量)

## Top Results

| Rank | score | total_return_pct | max_drawdown_pct | sharpe | profit_factor | session_count | session_win_rate | grid_close_win_rate | forced_exit_ratio | defensive_mode_ratio | SESSION_ABSOLUTE_MAX_AGE_BARS | SESSION_HARD_STOP_PCT | SESSION_MAX_AGE_BARS | SESSION_SOFT_STOP_PCT | SESSION_TRAILING_GIVEBACK_RATIO | TREND_RISK_SCORE_DEFENSIVE | average_session_pnl | avg_trades_per_session | diag_avg_realized_session | diag_avg_unrealized_session | diag_base_exit_loss | diag_buy_fills_per_session | diag_grid_close_count | diag_grid_pnl_total | diag_sell_fills_per_session | diag_sessions_with_grid_sell | exit_reason_breakdown | forced_exit_count | max_session_loss | profit_protect_exit | timeout_exit | total_grid_sessions | total_trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | -0.476 | 7.593 | 31.957 | -0.185 | 1.120 | 58 | 44.828 | 45.000 | 25.862 | 0.000 | 40 | 0.03 | 24 | 0.015 | 0.5 | 65 | 14.241423498948562 | 1.8103448275862069 | 1.106369247676136 | 13.135054251272416 | -2619.8546424999076 | 0.46551724137931033 | 5 | -472.34826837806406 | 1.3448275862068966 | 4 | {"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1} | 15 | -405.12648000000013 | 0 | 11 | 59 | 165 |
| 2 | -0.476 | 7.593 | 31.957 | -0.185 | 1.120 | 58 | 44.828 | 45.000 | 25.862 | 0.000 | 40 | 0.03 | 24 | 0.015 | 0.5 | 70 | 14.241423498948562 | 1.8103448275862069 | 1.106369247676136 | 13.135054251272416 | -2619.8546424999076 | 0.46551724137931033 | 5 | -472.34826837806406 | 1.3448275862068966 | 4 | {"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1} | 15 | -405.12648000000013 | 0 | 11 | 59 | 165 |
| 3 | -0.476 | 7.593 | 31.957 | -0.185 | 1.120 | 58 | 44.828 | 45.000 | 25.862 | 0.000 | 40 | 0.03 | 24 | 0.015 | 0.5 | 75 | 14.241423498948562 | 1.8103448275862069 | 1.106369247676136 | 13.135054251272416 | -2619.8546424999076 | 0.46551724137931033 | 5 | -472.34826837806406 | 1.3448275862068966 | 4 | {"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1} | 15 | -405.12648000000013 | 0 | 11 | 59 | 165 |
| 4 | -0.476 | 7.593 | 31.957 | -0.185 | 1.120 | 58 | 44.828 | 45.000 | 25.862 | 0.000 | 40 | 0.03 | 30 | 0.015 | 0.5 | 65 | 14.241423498948562 | 1.8103448275862069 | 1.106369247676136 | 13.135054251272416 | -2619.8546424999076 | 0.46551724137931033 | 5 | -472.34826837806406 | 1.3448275862068966 | 4 | {"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1} | 15 | -405.12648000000013 | 0 | 11 | 59 | 165 |
| 5 | -0.476 | 7.593 | 31.957 | -0.185 | 1.120 | 58 | 44.828 | 45.000 | 25.862 | 0.000 | 40 | 0.03 | 30 | 0.015 | 0.5 | 70 | 14.241423498948562 | 1.8103448275862069 | 1.106369247676136 | 13.135054251272416 | -2619.8546424999076 | 0.46551724137931033 | 5 | -472.34826837806406 | 1.3448275862068966 | 4 | {"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1} | 15 | -405.12648000000013 | 0 | 11 | 59 | 165 |

## Per-top 诊断

解读:
- **exit_reason 分布**: session 退出原因占比. hard_stop / trend_risk / age 是主要 forced_exit 来源.
- **buy/sell fills/session**: 每 session 平均成交笔数. 太低 (<2) 说明 session 还没充分跑.
- **grid_close_count**: 网格平仓数 (GRID_SELL 配对). 0 说明 sell trigger 从未命中.
- **base_exit_loss vs grid_pnl**: 底仓退出的现金差 vs 网格交易盈亏. 哪一方主导亏损?
- **avg_realized vs avg_unrealized**: session 已实现 vs 浮盈差距, 揭示 'EXIT 时机' 问题.

### Top 1
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_SOFT_STOP_PCT=0.015, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=14.241423498948562, avg_trades_per_session=1.8103448275862069, diag_avg_realized_session=1.106369247676136, diag_avg_unrealized_session=13.135054251272416, diag_base_exit_loss=-2619.8546424999076, diag_buy_fills_per_session=0.46551724137931033, diag_grid_close_count=5, diag_grid_pnl_total=-472.34826837806406, diag_sell_fills_per_session=1.3448275862068966, diag_sessions_with_grid_sell=4, exit_reason_breakdown={"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1}, forced_exit_count=15, max_session_loss=-405.12648000000013, profit_protect_exit=0, timeout_exit=11, total_grid_sessions=59, total_trades=165`
- score=-0.476 | total_return=7.59% | dd=31.96% | sharpe=-0.185
- sessions=58 | session_win_rate=44.8% | profit_factor=1.12
- **exit_reason**: profit_protect=31 (53%), hard_stop=15 (26%), age=11 (19%), price_deviation=1 (2%)
- forced_exit_ratio=25.9% | defensive_mode_ratio=0.0% | profit_protect_exit=0 | timeout_exit=11
- **buy_fills/session**=0.47 | **sell_fills/session**=1.34
- **grid_close_count**=5 (sessions with grid_sell=4) | grid_close_win_rate=45.0%
- grid_pnl_total=$-472.35 | base_exit_loss=$-2619.85
- avg_realized/session=$+1.11 | avg_unrealized/session=$+13.14

### Top 2
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_SOFT_STOP_PCT=0.015, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=14.241423498948562, avg_trades_per_session=1.8103448275862069, diag_avg_realized_session=1.106369247676136, diag_avg_unrealized_session=13.135054251272416, diag_base_exit_loss=-2619.8546424999076, diag_buy_fills_per_session=0.46551724137931033, diag_grid_close_count=5, diag_grid_pnl_total=-472.34826837806406, diag_sell_fills_per_session=1.3448275862068966, diag_sessions_with_grid_sell=4, exit_reason_breakdown={"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1}, forced_exit_count=15, max_session_loss=-405.12648000000013, profit_protect_exit=0, timeout_exit=11, total_grid_sessions=59, total_trades=165`
- score=-0.476 | total_return=7.59% | dd=31.96% | sharpe=-0.185
- sessions=58 | session_win_rate=44.8% | profit_factor=1.12
- **exit_reason**: profit_protect=31 (53%), hard_stop=15 (26%), age=11 (19%), price_deviation=1 (2%)
- forced_exit_ratio=25.9% | defensive_mode_ratio=0.0% | profit_protect_exit=0 | timeout_exit=11
- **buy_fills/session**=0.47 | **sell_fills/session**=1.34
- **grid_close_count**=5 (sessions with grid_sell=4) | grid_close_win_rate=45.0%
- grid_pnl_total=$-472.35 | base_exit_loss=$-2619.85
- avg_realized/session=$+1.11 | avg_unrealized/session=$+13.14

### Top 3
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_SOFT_STOP_PCT=0.015, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=75, average_session_pnl=14.241423498948562, avg_trades_per_session=1.8103448275862069, diag_avg_realized_session=1.106369247676136, diag_avg_unrealized_session=13.135054251272416, diag_base_exit_loss=-2619.8546424999076, diag_buy_fills_per_session=0.46551724137931033, diag_grid_close_count=5, diag_grid_pnl_total=-472.34826837806406, diag_sell_fills_per_session=1.3448275862068966, diag_sessions_with_grid_sell=4, exit_reason_breakdown={"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1}, forced_exit_count=15, max_session_loss=-405.12648000000013, profit_protect_exit=0, timeout_exit=11, total_grid_sessions=59, total_trades=165`
- score=-0.476 | total_return=7.59% | dd=31.96% | sharpe=-0.185
- sessions=58 | session_win_rate=44.8% | profit_factor=1.12
- **exit_reason**: profit_protect=31 (53%), hard_stop=15 (26%), age=11 (19%), price_deviation=1 (2%)
- forced_exit_ratio=25.9% | defensive_mode_ratio=0.0% | profit_protect_exit=0 | timeout_exit=11
- **buy_fills/session**=0.47 | **sell_fills/session**=1.34
- **grid_close_count**=5 (sessions with grid_sell=4) | grid_close_win_rate=45.0%
- grid_pnl_total=$-472.35 | base_exit_loss=$-2619.85
- avg_realized/session=$+1.11 | avg_unrealized/session=$+13.14

### Top 4
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=30, SESSION_SOFT_STOP_PCT=0.015, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=14.241423498948562, avg_trades_per_session=1.8103448275862069, diag_avg_realized_session=1.106369247676136, diag_avg_unrealized_session=13.135054251272416, diag_base_exit_loss=-2619.8546424999076, diag_buy_fills_per_session=0.46551724137931033, diag_grid_close_count=5, diag_grid_pnl_total=-472.34826837806406, diag_sell_fills_per_session=1.3448275862068966, diag_sessions_with_grid_sell=4, exit_reason_breakdown={"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1}, forced_exit_count=15, max_session_loss=-405.12648000000013, profit_protect_exit=0, timeout_exit=11, total_grid_sessions=59, total_trades=165`
- score=-0.476 | total_return=7.59% | dd=31.96% | sharpe=-0.185
- sessions=58 | session_win_rate=44.8% | profit_factor=1.12
- **exit_reason**: profit_protect=31 (53%), hard_stop=15 (26%), age=11 (19%), price_deviation=1 (2%)
- forced_exit_ratio=25.9% | defensive_mode_ratio=0.0% | profit_protect_exit=0 | timeout_exit=11
- **buy_fills/session**=0.47 | **sell_fills/session**=1.34
- **grid_close_count**=5 (sessions with grid_sell=4) | grid_close_win_rate=45.0%
- grid_pnl_total=$-472.35 | base_exit_loss=$-2619.85
- avg_realized/session=$+1.11 | avg_unrealized/session=$+13.14

### Top 5
- Params: `SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=30, SESSION_SOFT_STOP_PCT=0.015, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=14.241423498948562, avg_trades_per_session=1.8103448275862069, diag_avg_realized_session=1.106369247676136, diag_avg_unrealized_session=13.135054251272416, diag_base_exit_loss=-2619.8546424999076, diag_buy_fills_per_session=0.46551724137931033, diag_grid_close_count=5, diag_grid_pnl_total=-472.34826837806406, diag_sell_fills_per_session=1.3448275862068966, diag_sessions_with_grid_sell=4, exit_reason_breakdown={"profit_protect": 31, "hard_stop": 15, "age": 11, "price_deviation": 1}, forced_exit_count=15, max_session_loss=-405.12648000000013, profit_protect_exit=0, timeout_exit=11, total_grid_sessions=59, total_trades=165`
- score=-0.476 | total_return=7.59% | dd=31.96% | sharpe=-0.185
- sessions=58 | session_win_rate=44.8% | profit_factor=1.12
- **exit_reason**: profit_protect=31 (53%), hard_stop=15 (26%), age=11 (19%), price_deviation=1 (2%)
- forced_exit_ratio=25.9% | defensive_mode_ratio=0.0% | profit_protect_exit=0 | timeout_exit=11
- **buy_fills/session**=0.47 | **sell_fills/session**=1.34
- **grid_close_count**=5 (sessions with grid_sell=4) | grid_close_win_rate=45.0%
- grid_pnl_total=$-472.35 | base_exit_loss=$-2619.85
- avg_realized/session=$+1.11 | avg_unrealized/session=$+13.14


## 跨 top 整体诊断

整个 top 集合的 exit_reason 主导分布:
- `profit_protect`: 155 (53.4%)
- `hard_stop`: 75 (25.9%)
- `age`: 55 (19.0%)
- `price_deviation`: 5 (1.7%)

### 自动建议

- 🚨 hard_stop 占比 > 25%: 当前 SESSION_HARD_STOP_PCT 可能仍偏紧, 下一轮 P0 应继续放宽.
- ℹ️ age 退出占比偏高: 考虑加大 SESSION_ABSOLUTE_MAX_AGE_BARS.

## 下一步建议 (固定流程)

1. 不要直接把 top-1 参数写回 config.py — 先 walk-forward 验证 (--walk-forward)
2. P0 跑完后, 在 top-1 附近跑 P1 (--anchor-params JSON of top-1 P0 params)
3. P0+P1 top 3 邻域跑 P2 light
4. 检查每行 forced_exit_ratio — 过高 (>50%) 说明参数仍偏紧
5. profit_factor < 1.0 即使 total_return 为正, 也说明仰仗少数大盈利, 不稳健