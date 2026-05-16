# Tactical Tune — Profile **p2**

- Symbol: `UVXY` | Interval: `4h` | Years: `5.0`
- Total combinations explored: **32**
- Rows passing filters (sessions ≥ 5, dd < 50%): **5**
- Generated at: 2026-05-14T00:18:32.197681

## Scoring

score = annual_ret + 0.5×sharpe + 1.5×(profit_factor−1) − drawdown − forced_exit_ratio
  - session_count < 20 折半 (鼓励样本量)

## Top Results

| Rank | score | total_return_pct | max_drawdown_pct | sharpe | profit_factor | session_count | session_win_rate | grid_close_win_rate | forced_exit_ratio | defensive_mode_ratio | DEFENSIVE_MIN_REBOUND_ATR_TO_SELL | ENTRY_MAX_ADX | ENTRY_MAX_EMA_DEVIATION_ATR | EXIT_MAX_ADX | EXIT_PRICE_DEVIATION_ATR | GRID_SPACING_ATR_MULTIPLIER | SESSION_ABSOLUTE_MAX_AGE_BARS | SESSION_HARD_STOP_PCT | SESSION_MAX_AGE_BARS | SESSION_MAX_BUY_LEVELS_NORMAL | SESSION_MAX_POSITION_VALUE_PCT_NORMAL | SESSION_MIN_PROFIT_TO_PROTECT_PCT | SESSION_SOFT_STOP_PCT | SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO | SESSION_STRONG_PROFIT_PCT | SESSION_TRAILING_GIVEBACK_RATIO | TREND_RISK_SCORE_DEFENSIVE | average_session_pnl | avg_trades_per_session | diag_avg_realized_session | diag_avg_unrealized_session | diag_base_exit_loss | diag_buy_fills_per_session | diag_grid_close_count | diag_grid_pnl_total | diag_sell_fills_per_session | diag_sessions_with_grid_sell | exit_reason_breakdown | forced_exit_count | max_session_loss | profit_protect_exit | timeout_exit | total_grid_sessions | total_trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | -0.081 | 19.356 | 30.590 | -0.064 | 1.295 | 81 | 48.148 | 50.000 | 22.222 | 0.000 | 0.3 | 25 | 1.0 | 22 | 4.0 | 0.5 | 40 | 0.03 | 24 | 3 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 28.302414327970816 | 1.691358024691358 | 1.1183858124333108 | 27.184028515537495 | -1420.3098099997733 | 0.37037037037037035 | 7 | -393.8723419491117 | 1.3209876543209877 | 6 | {"profit_protect": 27, "age": 14, "hard_stop": 18, "adx_high": 21, "price_deviation": 1} | 18 | -388.0534400000002 | 27 | 14 | 82 | 220 |
| 2 | -0.112 | 17.381 | 29.565 | -0.084 | 1.275 | 82 | 48.780 | 52.174 | 21.951 | 0.000 | 0.3 | 25 | 1.0 | 22 | 4.0 | 0.6 | 40 | 0.03 | 24 | 3 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 25.829053296727537 | 1.5975609756097562 | 0.8546730454687969 | 24.974380251258737 | -1713.571029999759 | 0.3170731707317073 | 5 | -299.1335280192065 | 1.2804878048780488 | 5 | {"profit_protect": 28, "age": 14, "hard_stop": 18, "adx_high": 21, "price_deviation": 1} | 18 | -388.0534400000002 | 28 | 14 | 83 | 215 |
| 3 | -0.144 | 13.987 | 30.590 | -0.108 | 1.274 | 81 | 48.148 | 50.000 | 22.222 | 0.000 | 0.3 | 25 | 1.0 | 22 | 5.0 | 0.5 | 40 | 0.03 | 24 | 3 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 26.256303216859703 | 1.691358024691358 | 1.1183858124333108 | 25.13791740442638 | -1957.251204999804 | 0.37037037037037035 | 7 | -393.8723419491117 | 1.3209876543209877 | 6 | {"profit_protect": 28, "age": 14, "hard_stop": 18, "adx_high": 21} | 18 | -388.0534400000002 | 28 | 14 | 82 | 220 |
| 4 | -0.176 | 12.012 | 29.565 | -0.128 | 1.253 | 82 | 48.780 | 52.174 | 21.951 | 0.000 | 0.3 | 25 | 1.0 | 22 | 5.0 | 0.6 | 40 | 0.03 | 24 | 3 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 23.80789476014217 | 1.5975609756097562 | 0.8546730454687969 | 22.95322171467337 | -2250.51242499979 | 0.3170731707317073 | 5 | -299.1335280192065 | 1.2804878048780488 | 5 | {"profit_protect": 29, "age": 14, "hard_stop": 18, "adx_high": 21} | 18 | -388.0534400000002 | 29 | 14 | 83 | 215 |
| 5 | -0.290 | 9.515 | 36.692 | -0.135 | 1.225 | 90 | 48.889 | 53.571 | 21.111 | 0.000 | 0.3 | 25 | 1.5 | 22 | 4.0 | 0.6 | 40 | 0.03 | 24 | 3 | 0.45 | 0.01 | 0.015 | 0.25 | 0.012 | 0.5 | 70 | 22.270484516212314 | 1.6666666666666667 | 1.3939065583332053 | 20.87657795787912 | -2939.553699999873 | 0.35555555555555557 | 8 | -226.50202183252875 | 1.3111111111111111 | 6 | {"profit_protect": 28, "age": 18, "hard_stop": 19, "adx_high": 24, "price_deviation": 1} | 19 | -456.1211670000017 | 28 | 18 | 91 | 242 |

## Per-top 诊断

解读:
- **exit_reason 分布**: session 退出原因占比. hard_stop / trend_risk / age 是主要 forced_exit 来源.
- **buy/sell fills/session**: 每 session 平均成交笔数. 太低 (<2) 说明 session 还没充分跑.
- **grid_close_count**: 网格平仓数 (GRID_SELL 配对). 0 说明 sell trigger 从未命中.
- **base_exit_loss vs grid_pnl**: 底仓退出的现金差 vs 网格交易盈亏. 哪一方主导亏损?
- **avg_realized vs avg_unrealized**: session 已实现 vs 浮盈差距, 揭示 'EXIT 时机' 问题.

### Top 1
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.0, EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=4.0, GRID_SPACING_ATR_MULTIPLIER=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=3, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=28.302414327970816, avg_trades_per_session=1.691358024691358, diag_avg_realized_session=1.1183858124333108, diag_avg_unrealized_session=27.184028515537495, diag_base_exit_loss=-1420.3098099997733, diag_buy_fills_per_session=0.37037037037037035, diag_grid_close_count=7, diag_grid_pnl_total=-393.8723419491117, diag_sell_fills_per_session=1.3209876543209877, diag_sessions_with_grid_sell=6, exit_reason_breakdown={"profit_protect": 27, "age": 14, "hard_stop": 18, "adx_high": 21, "price_deviation": 1}, forced_exit_count=18, max_session_loss=-388.0534400000002, profit_protect_exit=27, timeout_exit=14, total_grid_sessions=82, total_trades=220`
- score=-0.081 | total_return=19.36% | dd=30.59% | sharpe=-0.064
- sessions=81 | session_win_rate=48.1% | profit_factor=1.30
- **exit_reason**: profit_protect=27 (33%), adx_high=21 (26%), hard_stop=18 (22%), age=14 (17%), price_deviation=1 (1%)
- forced_exit_ratio=22.2% | defensive_mode_ratio=0.0% | profit_protect_exit=27 | timeout_exit=14
- **buy_fills/session**=0.37 | **sell_fills/session**=1.32
- **grid_close_count**=7 (sessions with grid_sell=6) | grid_close_win_rate=50.0%
- grid_pnl_total=$-393.87 | base_exit_loss=$-1420.31
- avg_realized/session=$+1.12 | avg_unrealized/session=$+27.18

### Top 2
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.0, EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=4.0, GRID_SPACING_ATR_MULTIPLIER=0.6, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=3, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=25.829053296727537, avg_trades_per_session=1.5975609756097562, diag_avg_realized_session=0.8546730454687969, diag_avg_unrealized_session=24.974380251258737, diag_base_exit_loss=-1713.571029999759, diag_buy_fills_per_session=0.3170731707317073, diag_grid_close_count=5, diag_grid_pnl_total=-299.1335280192065, diag_sell_fills_per_session=1.2804878048780488, diag_sessions_with_grid_sell=5, exit_reason_breakdown={"profit_protect": 28, "age": 14, "hard_stop": 18, "adx_high": 21, "price_deviation": 1}, forced_exit_count=18, max_session_loss=-388.0534400000002, profit_protect_exit=28, timeout_exit=14, total_grid_sessions=83, total_trades=215`
- score=-0.112 | total_return=17.38% | dd=29.57% | sharpe=-0.084
- sessions=82 | session_win_rate=48.8% | profit_factor=1.27
- **exit_reason**: profit_protect=28 (34%), adx_high=21 (26%), hard_stop=18 (22%), age=14 (17%), price_deviation=1 (1%)
- forced_exit_ratio=22.0% | defensive_mode_ratio=0.0% | profit_protect_exit=28 | timeout_exit=14
- **buy_fills/session**=0.32 | **sell_fills/session**=1.28
- **grid_close_count**=5 (sessions with grid_sell=5) | grid_close_win_rate=52.2%
- grid_pnl_total=$-299.13 | base_exit_loss=$-1713.57
- avg_realized/session=$+0.85 | avg_unrealized/session=$+24.97

### Top 3
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.0, EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=5.0, GRID_SPACING_ATR_MULTIPLIER=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=3, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=26.256303216859703, avg_trades_per_session=1.691358024691358, diag_avg_realized_session=1.1183858124333108, diag_avg_unrealized_session=25.13791740442638, diag_base_exit_loss=-1957.251204999804, diag_buy_fills_per_session=0.37037037037037035, diag_grid_close_count=7, diag_grid_pnl_total=-393.8723419491117, diag_sell_fills_per_session=1.3209876543209877, diag_sessions_with_grid_sell=6, exit_reason_breakdown={"profit_protect": 28, "age": 14, "hard_stop": 18, "adx_high": 21}, forced_exit_count=18, max_session_loss=-388.0534400000002, profit_protect_exit=28, timeout_exit=14, total_grid_sessions=82, total_trades=220`
- score=-0.144 | total_return=13.99% | dd=30.59% | sharpe=-0.108
- sessions=81 | session_win_rate=48.1% | profit_factor=1.27
- **exit_reason**: profit_protect=28 (35%), adx_high=21 (26%), hard_stop=18 (22%), age=14 (17%)
- forced_exit_ratio=22.2% | defensive_mode_ratio=0.0% | profit_protect_exit=28 | timeout_exit=14
- **buy_fills/session**=0.37 | **sell_fills/session**=1.32
- **grid_close_count**=7 (sessions with grid_sell=6) | grid_close_win_rate=50.0%
- grid_pnl_total=$-393.87 | base_exit_loss=$-1957.25
- avg_realized/session=$+1.12 | avg_unrealized/session=$+25.14

### Top 4
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.0, EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=5.0, GRID_SPACING_ATR_MULTIPLIER=0.6, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=3, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=23.80789476014217, avg_trades_per_session=1.5975609756097562, diag_avg_realized_session=0.8546730454687969, diag_avg_unrealized_session=22.95322171467337, diag_base_exit_loss=-2250.51242499979, diag_buy_fills_per_session=0.3170731707317073, diag_grid_close_count=5, diag_grid_pnl_total=-299.1335280192065, diag_sell_fills_per_session=1.2804878048780488, diag_sessions_with_grid_sell=5, exit_reason_breakdown={"profit_protect": 29, "age": 14, "hard_stop": 18, "adx_high": 21}, forced_exit_count=18, max_session_loss=-388.0534400000002, profit_protect_exit=29, timeout_exit=14, total_grid_sessions=83, total_trades=215`
- score=-0.176 | total_return=12.01% | dd=29.57% | sharpe=-0.128
- sessions=82 | session_win_rate=48.8% | profit_factor=1.25
- **exit_reason**: profit_protect=29 (35%), adx_high=21 (26%), hard_stop=18 (22%), age=14 (17%)
- forced_exit_ratio=22.0% | defensive_mode_ratio=0.0% | profit_protect_exit=29 | timeout_exit=14
- **buy_fills/session**=0.32 | **sell_fills/session**=1.28
- **grid_close_count**=5 (sessions with grid_sell=5) | grid_close_win_rate=52.2%
- grid_pnl_total=$-299.13 | base_exit_loss=$-2250.51
- avg_realized/session=$+0.85 | avg_unrealized/session=$+22.95

### Top 5
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.5, EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=4.0, GRID_SPACING_ATR_MULTIPLIER=0.6, SESSION_ABSOLUTE_MAX_AGE_BARS=40, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=24, SESSION_MAX_BUY_LEVELS_NORMAL=3, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.01, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=70, average_session_pnl=22.270484516212314, avg_trades_per_session=1.6666666666666667, diag_avg_realized_session=1.3939065583332053, diag_avg_unrealized_session=20.87657795787912, diag_base_exit_loss=-2939.553699999873, diag_buy_fills_per_session=0.35555555555555557, diag_grid_close_count=8, diag_grid_pnl_total=-226.50202183252875, diag_sell_fills_per_session=1.3111111111111111, diag_sessions_with_grid_sell=6, exit_reason_breakdown={"profit_protect": 28, "age": 18, "hard_stop": 19, "adx_high": 24, "price_deviation": 1}, forced_exit_count=19, max_session_loss=-456.1211670000017, profit_protect_exit=28, timeout_exit=18, total_grid_sessions=91, total_trades=242`
- score=-0.290 | total_return=9.52% | dd=36.69% | sharpe=-0.135
- sessions=90 | session_win_rate=48.9% | profit_factor=1.22
- **exit_reason**: profit_protect=28 (31%), adx_high=24 (27%), hard_stop=19 (21%), age=18 (20%), price_deviation=1 (1%)
- forced_exit_ratio=21.1% | defensive_mode_ratio=0.0% | profit_protect_exit=28 | timeout_exit=18
- **buy_fills/session**=0.36 | **sell_fills/session**=1.31
- **grid_close_count**=8 (sessions with grid_sell=6) | grid_close_win_rate=53.6%
- grid_pnl_total=$-226.50 | base_exit_loss=$-2939.55
- avg_realized/session=$+1.39 | avg_unrealized/session=$+20.88


## 跨 top 整体诊断

整个 top 集合的 exit_reason 主导分布:
- `profit_protect`: 140 (33.7%)
- `adx_high`: 108 (26.0%)
- `hard_stop`: 91 (21.9%)
- `age`: 74 (17.8%)
- `price_deviation`: 3 (0.7%)

### 自动建议

- ℹ️ age 退出占比偏高: 考虑加大 SESSION_ABSOLUTE_MAX_AGE_BARS.

## 下一步建议 (固定流程)

1. 不要直接把 top-1 参数写回 config.py — 先 walk-forward 验证 (--walk-forward)
2. P0 跑完后, 在 top-1 附近跑 P1 (--anchor-params JSON of top-1 P0 params)
3. P0+P1 top 3 邻域跑 P2 light
4. 检查每行 forced_exit_ratio — 过高 (>50%) 说明参数仍偏紧
5. profit_factor < 1.0 即使 total_return 为正, 也说明仰仗少数大盈利, 不稳健