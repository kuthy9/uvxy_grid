# Tactical Tune — Profile **p1**

- Symbol: `UVXY` | Interval: `4h` | Years: `5.0`
- Total combinations explored: **144**
- Rows passing filters (sessions ≥ 5, dd < 50%): **5**
- Generated at: 2026-05-14T00:16:06.252258

## Scoring

score = annual_ret + 0.5×sharpe + 1.5×(profit_factor−1) − drawdown − forced_exit_ratio
  - session_count < 20 折半 (鼓励样本量)

## Top Results

| Rank | score | total_return_pct | max_drawdown_pct | sharpe | profit_factor | session_count | session_win_rate | grid_close_win_rate | forced_exit_ratio | defensive_mode_ratio | DEFENSIVE_MIN_REBOUND_ATR_TO_SELL | SESSION_ABSOLUTE_MAX_AGE_BARS | SESSION_HARD_STOP_PCT | SESSION_MAX_AGE_BARS | SESSION_MAX_BUY_LEVELS_NORMAL | SESSION_MAX_POSITION_VALUE_PCT_NORMAL | SESSION_MIN_PROFIT_TO_PROTECT_PCT | SESSION_SOFT_STOP_PCT | SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO | SESSION_STRONG_PROFIT_PCT | SESSION_TRAILING_GIVEBACK_RATIO | TREND_RISK_SCORE_DEFENSIVE | average_session_pnl | avg_trades_per_session | diag_avg_realized_session | diag_avg_unrealized_session | diag_base_exit_loss | diag_buy_fills_per_session | diag_grid_close_count | diag_grid_pnl_total | diag_sell_fills_per_session | diag_sessions_with_grid_sell | exit_reason_breakdown | forced_exit_count | max_session_loss | profit_protect_exit | timeout_exit | total_grid_sessions | total_trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | -0.427 | 9.901 | 32.817 | -0.157 | 1.184 | 57 | 47.368 | 54.167 | 31.579 | 0.000 | 0.3 | 40 | 0.03 | 24 | 3 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 23.273593078240904 | 1.894736842105263 | 1.6782187302746414 | 21.595374347966256 | -2440.3015524999355 | 0.47368421052631576 | 7 | -423.07548109236535 | 1.4210526315789473 | 6 | {"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1} | 18 | -405.12648000000013 | 25 | 13 | 58 | 167 |
| 2 | -0.427 | 9.901 | 32.817 | -0.157 | 1.184 | 57 | 47.368 | 54.167 | 31.579 | 0.000 | 0.5 | 40 | 0.03 | 24 | 3 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 23.273593078240904 | 1.894736842105263 | 1.6782187302746414 | 21.595374347966256 | -2440.3015524999355 | 0.47368421052631576 | 7 | -423.07548109236535 | 1.4210526315789473 | 6 | {"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1} | 18 | -405.12648000000013 | 25 | 13 | 58 | 167 |
| 3 | -0.427 | 9.901 | 32.817 | -0.157 | 1.184 | 57 | 47.368 | 54.167 | 31.579 | 0.000 | 0.3 | 40 | 0.03 | 24 | 4 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 23.273593078240904 | 1.894736842105263 | 1.6782187302746414 | 21.595374347966256 | -2440.3015524999355 | 0.47368421052631576 | 7 | -423.07548109236535 | 1.4210526315789473 | 6 | {"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1} | 18 | -405.12648000000013 | 25 | 13 | 58 | 167 |
| 4 | -0.427 | 9.901 | 32.817 | -0.157 | 1.184 | 57 | 47.368 | 54.167 | 31.579 | 0.000 | 0.5 | 40 | 0.03 | 24 | 4 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 23.273593078240904 | 1.894736842105263 | 1.6782187302746414 | 21.595374347966256 | -2440.3015524999355 | 0.47368421052631576 | 7 | -423.07548109236535 | 1.4210526315789473 | 6 | {"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1} | 18 | -405.12648000000013 | 25 | 13 | 58 | 167 |
| 5 | -0.427 | 9.901 | 32.817 | -0.157 | 1.184 | 57 | 47.368 | 54.167 | 31.579 | 0.000 | 0.3 | 40 | 0.03 | 24 | 3 | 0.45 | 0.01 | 0.015 | 0.35 | 0.012 | 0.5 | 70 | 23.273593078240904 | 1.894736842105263 | 1.6782187302746414 | 21.595374347966256 | -2440.3015524999355 | 0.47368421052631576 | 7 | -423.07548109236535 | 1.4210526315789473 | 6 | {"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1} | 18 | -405.12648000000013 | 25 | 13 | 58 | 167 |

## Per-top 诊断

解读:
- **exit_reason 分布**: session 退出原因占比. hard_stop / trend_risk / age 是主要 forced_exit 来源.
- **buy/sell fills/session**: 每 session 平均成交笔数. 太低 (<2) 说明 session 还没充分跑.
- **grid_close_count**: 网格平仓数 (GRID_SELL 配对). 0 说明 sell trigger 从未命中.
- **base_exit_loss vs grid_pnl**: 底仓退出的现金差 vs 网格交易盈亏. 哪一方主导亏损?
- **avg_realized vs avg_unrealized**: session 已实现 vs 浮盈差距, 揭示 'EXIT 时机' 问题.

### Top 1
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=3, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=23.273593078240904, avg_trades_per_session=1.894736842105263, diag_avg_realized_session=1.6782187302746414, diag_avg_unrealized_session=21.595374347966256, diag_base_exit_loss=-2440.3015524999355, diag_buy_fills_per_session=0.47368421052631576, diag_grid_close_count=7, diag_grid_pnl_total=-423.07548109236535, diag_sell_fills_per_session=1.4210526315789473, diag_sessions_with_grid_sell=6, exit_reason_breakdown={"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1}, forced_exit_count=18, max_session_loss=-405.12648000000013, profit_protect_exit=25, timeout_exit=13, total_grid_sessions=58, total_trades=167`
- score=-0.427 | total_return=9.90% | dd=32.82% | sharpe=-0.157
- sessions=57 | session_win_rate=47.4% | profit_factor=1.18
- **exit_reason**: profit_protect=25 (44%), hard_stop=18 (32%), age=13 (23%), price_deviation=1 (2%)
- forced_exit_ratio=31.6% | defensive_mode_ratio=0.0% | profit_protect_exit=25 | timeout_exit=13
- **buy_fills/session**=0.47 | **sell_fills/session**=1.42
- **grid_close_count**=7 (sessions with grid_sell=6) | grid_close_win_rate=54.2%
- grid_pnl_total=$-423.08 | base_exit_loss=$-2440.30
- avg_realized/session=$+1.68 | avg_unrealized/session=$+21.60

### Top 2
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=3, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=23.273593078240904, avg_trades_per_session=1.894736842105263, diag_avg_realized_session=1.6782187302746414, diag_avg_unrealized_session=21.595374347966256, diag_base_exit_loss=-2440.3015524999355, diag_buy_fills_per_session=0.47368421052631576, diag_grid_close_count=7, diag_grid_pnl_total=-423.07548109236535, diag_sell_fills_per_session=1.4210526315789473, diag_sessions_with_grid_sell=6, exit_reason_breakdown={"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1}, forced_exit_count=18, max_session_loss=-405.12648000000013, profit_protect_exit=25, timeout_exit=13, total_grid_sessions=58, total_trades=167`
- score=-0.427 | total_return=9.90% | dd=32.82% | sharpe=-0.157
- sessions=57 | session_win_rate=47.4% | profit_factor=1.18
- **exit_reason**: profit_protect=25 (44%), hard_stop=18 (32%), age=13 (23%), price_deviation=1 (2%)
- forced_exit_ratio=31.6% | defensive_mode_ratio=0.0% | profit_protect_exit=25 | timeout_exit=13
- **buy_fills/session**=0.47 | **sell_fills/session**=1.42
- **grid_close_count**=7 (sessions with grid_sell=6) | grid_close_win_rate=54.2%
- grid_pnl_total=$-423.08 | base_exit_loss=$-2440.30
- avg_realized/session=$+1.68 | avg_unrealized/session=$+21.60

### Top 3
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=23.273593078240904, avg_trades_per_session=1.894736842105263, diag_avg_realized_session=1.6782187302746414, diag_avg_unrealized_session=21.595374347966256, diag_base_exit_loss=-2440.3015524999355, diag_buy_fills_per_session=0.47368421052631576, diag_grid_close_count=7, diag_grid_pnl_total=-423.07548109236535, diag_sell_fills_per_session=1.4210526315789473, diag_sessions_with_grid_sell=6, exit_reason_breakdown={"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1}, forced_exit_count=18, max_session_loss=-405.12648000000013, profit_protect_exit=25, timeout_exit=13, total_grid_sessions=58, total_trades=167`
- score=-0.427 | total_return=9.90% | dd=32.82% | sharpe=-0.157
- sessions=57 | session_win_rate=47.4% | profit_factor=1.18
- **exit_reason**: profit_protect=25 (44%), hard_stop=18 (32%), age=13 (23%), price_deviation=1 (2%)
- forced_exit_ratio=31.6% | defensive_mode_ratio=0.0% | profit_protect_exit=25 | timeout_exit=13
- **buy_fills/session**=0.47 | **sell_fills/session**=1.42
- **grid_close_count**=7 (sessions with grid_sell=6) | grid_close_win_rate=54.2%
- grid_pnl_total=$-423.08 | base_exit_loss=$-2440.30
- avg_realized/session=$+1.68 | avg_unrealized/session=$+21.60

### Top 4
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=23.273593078240904, avg_trades_per_session=1.894736842105263, diag_avg_realized_session=1.6782187302746414, diag_avg_unrealized_session=21.595374347966256, diag_base_exit_loss=-2440.3015524999355, diag_buy_fills_per_session=0.47368421052631576, diag_grid_close_count=7, diag_grid_pnl_total=-423.07548109236535, diag_sell_fills_per_session=1.4210526315789473, diag_sessions_with_grid_sell=6, exit_reason_breakdown={"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1}, forced_exit_count=18, max_session_loss=-405.12648000000013, profit_protect_exit=25, timeout_exit=13, total_grid_sessions=58, total_trades=167`
- score=-0.427 | total_return=9.90% | dd=32.82% | sharpe=-0.157
- sessions=57 | session_win_rate=47.4% | profit_factor=1.18
- **exit_reason**: profit_protect=25 (44%), hard_stop=18 (32%), age=13 (23%), price_deviation=1 (2%)
- forced_exit_ratio=31.6% | defensive_mode_ratio=0.0% | profit_protect_exit=25 | timeout_exit=13
- **buy_fills/session**=0.47 | **sell_fills/session**=1.42
- **grid_close_count**=7 (sessions with grid_sell=6) | grid_close_win_rate=54.2%
- grid_pnl_total=$-423.08 | base_exit_loss=$-2440.30
- avg_realized/session=$+1.68 | avg_unrealized/session=$+21.60

### Top 5
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=3, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.35, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=23.273593078240904, avg_trades_per_session=1.894736842105263, diag_avg_realized_session=1.6782187302746414, diag_avg_unrealized_session=21.595374347966256, diag_base_exit_loss=-2440.3015524999355, diag_buy_fills_per_session=0.47368421052631576, diag_grid_close_count=7, diag_grid_pnl_total=-423.07548109236535, diag_sell_fills_per_session=1.4210526315789473, diag_sessions_with_grid_sell=6, exit_reason_breakdown={"profit_protect": 25, "age": 13, "hard_stop": 18, "price_deviation": 1}, forced_exit_count=18, max_session_loss=-405.12648000000013, profit_protect_exit=25, timeout_exit=13, total_grid_sessions=58, total_trades=167`
- score=-0.427 | total_return=9.90% | dd=32.82% | sharpe=-0.157
- sessions=57 | session_win_rate=47.4% | profit_factor=1.18
- **exit_reason**: profit_protect=25 (44%), hard_stop=18 (32%), age=13 (23%), price_deviation=1 (2%)
- forced_exit_ratio=31.6% | defensive_mode_ratio=0.0% | profit_protect_exit=25 | timeout_exit=13
- **buy_fills/session**=0.47 | **sell_fills/session**=1.42
- **grid_close_count**=7 (sessions with grid_sell=6) | grid_close_win_rate=54.2%
- grid_pnl_total=$-423.08 | base_exit_loss=$-2440.30
- avg_realized/session=$+1.68 | avg_unrealized/session=$+21.60


## 跨 top 整体诊断

整个 top 集合的 exit_reason 主导分布:
- `profit_protect`: 125 (43.9%)
- `hard_stop`: 90 (31.6%)
- `age`: 65 (22.8%)
- `price_deviation`: 5 (1.8%)

### 自动建议

- 🚨 hard_stop 占比 > 25%: 当前 SESSION_HARD_STOP_PCT 可能仍偏紧, 下一轮 P0 应继续放宽.
- ℹ️ age 退出占比偏高: 考虑加大 SESSION_ABSOLUTE_MAX_AGE_BARS.

## 下一步建议 (固定流程)

1. 不要直接把 top-1 参数写回 config.py — 先 walk-forward 验证 (--walk-forward)
2. P0 跑完后, 在 top-1 附近跑 P1 (--anchor-params JSON of top-1 P0 params)
3. P0+P1 top 3 邻域跑 P2 light
4. 检查每行 forced_exit_ratio — 过高 (>50%) 说明参数仍偏紧
5. profit_factor < 1.0 即使 total_return 为正, 也说明仰仗少数大盈利, 不稳健