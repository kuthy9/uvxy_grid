# Tactical Tune — Profile **p2**

- Symbol: `UVXY` | Interval: `4h` | Years: `5.0`
- Total combinations explored: **32**
- Rows passing filters (sessions ≥ 5, dd < 50%): **5**
- Generated at: 2026-05-14T01:54:59.948050

## Scoring

score = annual_ret + 0.5×sharpe + 1.5×(profit_factor−1) − drawdown − forced_exit_ratio
  - session_count < 20 折半 (鼓励样本量)

## Top Results

| Rank | score | total_return_pct | max_drawdown_pct | sharpe | profit_factor | session_count | session_win_rate | grid_close_win_rate | forced_exit_ratio | defensive_mode_ratio | DEFENSIVE_MIN_REBOUND_ATR_TO_SELL | ENTRY_MAX_ADX | ENTRY_MAX_EMA_DEVIATION_ATR | EXIT_MAX_ADX | EXIT_PRICE_DEVIATION_ATR | GRID_SPACING_ATR_MULTIPLIER | SESSION_ABSOLUTE_MAX_AGE_BARS | SESSION_HARD_STOP_PCT | SESSION_MAX_AGE_BARS | SESSION_MAX_BUY_LEVELS_NORMAL | SESSION_MAX_POSITION_VALUE_PCT_NORMAL | SESSION_MIN_PROFIT_TO_PROTECT_PCT | SESSION_SOFT_STOP_PCT | SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO | SESSION_STRONG_PROFIT_PCT | SESSION_TRAILING_GIVEBACK_RATIO | TREND_RISK_SCORE_DEFENSIVE | average_session_pnl | avg_trades_per_session | diag_avg_realized_session | diag_avg_unrealized_session | diag_base_exit_loss | diag_buy_fills_per_session | diag_grid_close_count | diag_grid_pnl_total | diag_sell_fills_per_session | diag_sessions_with_grid_sell | exit_reason_breakdown | forced_exit_count | max_session_loss | profit_protect_exit | timeout_exit | total_grid_sessions | total_trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | -1.569 | -4.386 | 6.757 | -1.627 | 0.547 | 54 | 40.741 | 65.000 | 0.000 | 0.000 | 0.3 | 25 | 1.5 | 22 | 4.0 | 0.5 | 60 | 0.03 | 40 | 4 | 0.55 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -13.933500188469507 | 3.1296296296296298 | 8.290218040931151 | -22.223718229400667 | 0.0 | 2.0185185185185186 | 22 | -436.3564974189136 | 1.1111111111111112 | 13 | {"age": 44, "profit_protect": 10} | 0 | -223.7814810000002 | 10 | 44 | 55 | 170 |
| 2 | -1.569 | -4.386 | 6.757 | -1.627 | 0.547 | 54 | 40.741 | 65.000 | 0.000 | 0.000 | 0.3 | 25 | 1.5 | 22 | 5.0 | 0.5 | 60 | 0.03 | 40 | 4 | 0.55 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -13.933500188469507 | 3.1296296296296298 | 8.290218040931151 | -22.223718229400667 | 0.0 | 2.0185185185185186 | 22 | -436.3564974189136 | 1.1111111111111112 | 13 | {"age": 44, "profit_protect": 10} | 0 | -223.7814810000002 | 10 | 44 | 55 | 170 |
| 3 | -1.569 | -4.386 | 6.757 | -1.627 | 0.547 | 54 | 40.741 | 65.000 | 0.000 | 0.000 | 0.3 | 25 | 1.5 | 28 | 4.0 | 0.5 | 60 | 0.03 | 40 | 4 | 0.55 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -13.933500188469507 | 3.1296296296296298 | 8.290218040931151 | -22.223718229400667 | 0.0 | 2.0185185185185186 | 22 | -436.3564974189136 | 1.1111111111111112 | 13 | {"age": 44, "profit_protect": 10} | 0 | -223.7814810000002 | 10 | 44 | 55 | 170 |
| 4 | -1.569 | -4.386 | 6.757 | -1.627 | 0.547 | 54 | 40.741 | 65.000 | 0.000 | 0.000 | 0.3 | 25 | 1.5 | 28 | 5.0 | 0.5 | 60 | 0.03 | 40 | 4 | 0.55 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -13.933500188469507 | 3.1296296296296298 | 8.290218040931151 | -22.223718229400667 | 0.0 | 2.0185185185185186 | 22 | -436.3564974189136 | 1.1111111111111112 | 13 | {"age": 44, "profit_protect": 10} | 0 | -223.7814810000002 | 10 | 44 | 55 | 170 |
| 5 | -1.646 | -3.694 | 6.079 | -1.877 | 0.574 | 55 | 34.545 | 62.000 | 0.000 | 0.000 | 0.3 | 25 | 1.5 | 22 | 4.0 | 0.6 | 60 | 0.03 | 40 | 4 | 0.55 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -11.145971832127273 | 2.5454545454545454 | 5.617908639250417 | -16.76388047137769 | 0.0 | 1.6363636363636365 | 14 | -368.91414087276655 | 0.9090909090909091 | 10 | {"age": 47, "profit_protect": 8} | 0 | -212.81038400000017 | 8 | 47 | 56 | 141 |

## Per-top 诊断

解读:
- **exit_reason 分布**: session 退出原因占比. hard_stop / trend_risk / age 是主要 forced_exit 来源.
- **buy/sell fills/session**: 每 session 平均成交笔数. 太低 (<2) 说明 session 还没充分跑.
- **grid_close_count**: 网格平仓数 (GRID_SELL 配对). 0 说明 sell trigger 从未命中.
- **base_exit_loss vs grid_pnl**: 底仓退出的现金差 vs 网格交易盈亏. 哪一方主导亏损?
- **avg_realized vs avg_unrealized**: session 已实现 vs 浮盈差距, 揭示 'EXIT 时机' 问题.

### Top 1
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.5, EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=4.0, GRID_SPACING_ATR_MULTIPLIER=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-13.933500188469507, avg_trades_per_session=3.1296296296296298, diag_avg_realized_session=8.290218040931151, diag_avg_unrealized_session=-22.223718229400667, diag_base_exit_loss=0.0, diag_buy_fills_per_session=2.0185185185185186, diag_grid_close_count=22, diag_grid_pnl_total=-436.3564974189136, diag_sell_fills_per_session=1.1111111111111112, diag_sessions_with_grid_sell=13, exit_reason_breakdown={"age": 44, "profit_protect": 10}, forced_exit_count=0, max_session_loss=-223.7814810000002, profit_protect_exit=10, timeout_exit=44, total_grid_sessions=55, total_trades=170`
- score=-1.569 | total_return=-4.39% | dd=6.76% | sharpe=-1.627
- sessions=54 | session_win_rate=40.7% | profit_factor=0.55
- **exit_reason**: age=44 (81%), profit_protect=10 (19%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=10 | timeout_exit=44
- **buy_fills/session**=2.02 | **sell_fills/session**=1.11
- **grid_close_count**=22 (sessions with grid_sell=13) | grid_close_win_rate=65.0%
- grid_pnl_total=$-436.36 | base_exit_loss=$+0.00
- avg_realized/session=$+8.29 | avg_unrealized/session=$-22.22

### Top 2
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.5, EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=5.0, GRID_SPACING_ATR_MULTIPLIER=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-13.933500188469507, avg_trades_per_session=3.1296296296296298, diag_avg_realized_session=8.290218040931151, diag_avg_unrealized_session=-22.223718229400667, diag_base_exit_loss=0.0, diag_buy_fills_per_session=2.0185185185185186, diag_grid_close_count=22, diag_grid_pnl_total=-436.3564974189136, diag_sell_fills_per_session=1.1111111111111112, diag_sessions_with_grid_sell=13, exit_reason_breakdown={"age": 44, "profit_protect": 10}, forced_exit_count=0, max_session_loss=-223.7814810000002, profit_protect_exit=10, timeout_exit=44, total_grid_sessions=55, total_trades=170`
- score=-1.569 | total_return=-4.39% | dd=6.76% | sharpe=-1.627
- sessions=54 | session_win_rate=40.7% | profit_factor=0.55
- **exit_reason**: age=44 (81%), profit_protect=10 (19%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=10 | timeout_exit=44
- **buy_fills/session**=2.02 | **sell_fills/session**=1.11
- **grid_close_count**=22 (sessions with grid_sell=13) | grid_close_win_rate=65.0%
- grid_pnl_total=$-436.36 | base_exit_loss=$+0.00
- avg_realized/session=$+8.29 | avg_unrealized/session=$-22.22

### Top 3
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.5, EXIT_MAX_ADX=28, EXIT_PRICE_DEVIATION_ATR=4.0, GRID_SPACING_ATR_MULTIPLIER=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-13.933500188469507, avg_trades_per_session=3.1296296296296298, diag_avg_realized_session=8.290218040931151, diag_avg_unrealized_session=-22.223718229400667, diag_base_exit_loss=0.0, diag_buy_fills_per_session=2.0185185185185186, diag_grid_close_count=22, diag_grid_pnl_total=-436.3564974189136, diag_sell_fills_per_session=1.1111111111111112, diag_sessions_with_grid_sell=13, exit_reason_breakdown={"age": 44, "profit_protect": 10}, forced_exit_count=0, max_session_loss=-223.7814810000002, profit_protect_exit=10, timeout_exit=44, total_grid_sessions=55, total_trades=170`
- score=-1.569 | total_return=-4.39% | dd=6.76% | sharpe=-1.627
- sessions=54 | session_win_rate=40.7% | profit_factor=0.55
- **exit_reason**: age=44 (81%), profit_protect=10 (19%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=10 | timeout_exit=44
- **buy_fills/session**=2.02 | **sell_fills/session**=1.11
- **grid_close_count**=22 (sessions with grid_sell=13) | grid_close_win_rate=65.0%
- grid_pnl_total=$-436.36 | base_exit_loss=$+0.00
- avg_realized/session=$+8.29 | avg_unrealized/session=$-22.22

### Top 4
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.5, EXIT_MAX_ADX=28, EXIT_PRICE_DEVIATION_ATR=5.0, GRID_SPACING_ATR_MULTIPLIER=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-13.933500188469507, avg_trades_per_session=3.1296296296296298, diag_avg_realized_session=8.290218040931151, diag_avg_unrealized_session=-22.223718229400667, diag_base_exit_loss=0.0, diag_buy_fills_per_session=2.0185185185185186, diag_grid_close_count=22, diag_grid_pnl_total=-436.3564974189136, diag_sell_fills_per_session=1.1111111111111112, diag_sessions_with_grid_sell=13, exit_reason_breakdown={"age": 44, "profit_protect": 10}, forced_exit_count=0, max_session_loss=-223.7814810000002, profit_protect_exit=10, timeout_exit=44, total_grid_sessions=55, total_trades=170`
- score=-1.569 | total_return=-4.39% | dd=6.76% | sharpe=-1.627
- sessions=54 | session_win_rate=40.7% | profit_factor=0.55
- **exit_reason**: age=44 (81%), profit_protect=10 (19%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=10 | timeout_exit=44
- **buy_fills/session**=2.02 | **sell_fills/session**=1.11
- **grid_close_count**=22 (sessions with grid_sell=13) | grid_close_win_rate=65.0%
- grid_pnl_total=$-436.36 | base_exit_loss=$+0.00
- avg_realized/session=$+8.29 | avg_unrealized/session=$-22.22

### Top 5
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, ENTRY_MAX_ADX=25, ENTRY_MAX_EMA_DEVIATION_ATR=1.5, EXIT_MAX_ADX=22, EXIT_PRICE_DEVIATION_ATR=4.0, GRID_SPACING_ATR_MULTIPLIER=0.6, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-11.145971832127273, avg_trades_per_session=2.5454545454545454, diag_avg_realized_session=5.617908639250417, diag_avg_unrealized_session=-16.76388047137769, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.6363636363636365, diag_grid_close_count=14, diag_grid_pnl_total=-368.91414087276655, diag_sell_fills_per_session=0.9090909090909091, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 47, "profit_protect": 8}, forced_exit_count=0, max_session_loss=-212.81038400000017, profit_protect_exit=8, timeout_exit=47, total_grid_sessions=56, total_trades=141`
- score=-1.646 | total_return=-3.69% | dd=6.08% | sharpe=-1.877
- sessions=55 | session_win_rate=34.5% | profit_factor=0.57
- **exit_reason**: age=47 (85%), profit_protect=8 (15%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=8 | timeout_exit=47
- **buy_fills/session**=1.64 | **sell_fills/session**=0.91
- **grid_close_count**=14 (sessions with grid_sell=10) | grid_close_win_rate=62.0%
- grid_pnl_total=$-368.91 | base_exit_loss=$+0.00
- avg_realized/session=$+5.62 | avg_unrealized/session=$-16.76


## 跨 top 整体诊断

整个 top 集合的 exit_reason 主导分布:
- `age`: 223 (82.3%)
- `profit_protect`: 48 (17.7%)

### 自动建议

- ℹ️ age 退出占比偏高: 考虑加大 SESSION_ABSOLUTE_MAX_AGE_BARS.
- ℹ️ avg_unrealized << avg_realized: session EXIT 时仍带较大浮亏, 可能 SOFT/HARD 触发节奏太早, 没等到反弹.

## 下一步建议 (固定流程)

1. 不要直接把 top-1 参数写回 config.py — 先 walk-forward 验证 (--walk-forward)
2. P0 跑完后, 在 top-1 附近跑 P1 (--anchor-params JSON of top-1 P0 params)
3. P0+P1 top 3 邻域跑 P2 light
4. 检查每行 forced_exit_ratio — 过高 (>50%) 说明参数仍偏紧
5. profit_factor < 1.0 即使 total_return 为正, 也说明仰仗少数大盈利, 不稳健