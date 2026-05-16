# Tactical Tune — Profile **p1**

- Symbol: `UVXY` | Interval: `4h` | Years: `5.0`
- Total combinations explored: **144**
- Rows passing filters (sessions ≥ 5, dd < 50%): **5**
- Generated at: 2026-05-14T01:51:52.402377

## Scoring

score = annual_ret + 0.5×sharpe + 1.5×(profit_factor−1) − drawdown − forced_exit_ratio
  - session_count < 20 折半 (鼓励样本量)

## Top Results

| Rank | score | total_return_pct | max_drawdown_pct | sharpe | profit_factor | session_count | session_win_rate | grid_close_win_rate | forced_exit_ratio | defensive_mode_ratio | DEFENSIVE_MIN_REBOUND_ATR_TO_SELL | SESSION_ABSOLUTE_MAX_AGE_BARS | SESSION_HARD_STOP_PCT | SESSION_MAX_AGE_BARS | SESSION_MAX_BUY_LEVELS_NORMAL | SESSION_MAX_POSITION_VALUE_PCT_NORMAL | SESSION_MIN_PROFIT_TO_PROTECT_PCT | SESSION_SOFT_STOP_PCT | SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO | SESSION_STRONG_PROFIT_PCT | SESSION_TRAILING_GIVEBACK_RATIO | TREND_RISK_SCORE_DEFENSIVE | average_session_pnl | avg_trades_per_session | diag_avg_realized_session | diag_avg_unrealized_session | diag_base_exit_loss | diag_buy_fills_per_session | diag_grid_close_count | diag_grid_pnl_total | diag_sell_fills_per_session | diag_sessions_with_grid_sell | exit_reason_breakdown | forced_exit_count | max_session_loss | profit_protect_exit | timeout_exit | total_grid_sessions | total_trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | -2.057 | -6.240 | 6.809 | -2.137 | 0.395 | 39 | 38.462 | 60.000 | 0.000 | 0.000 | 0.3 | 60 | 0.03 | 40 | 4 | 0.55 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -18.730528746889938 | 2.9743589743589745 | 8.973838299262749 | -27.70436704615269 | 0.0 | 1.8205128205128205 | 17 | -624.7835306702389 | 1.1538461538461537 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 117 |
| 2 | -2.057 | -6.240 | 6.809 | -2.137 | 0.395 | 39 | 38.462 | 60.000 | 0.000 | 0.000 | 0.5 | 60 | 0.03 | 40 | 4 | 0.45 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -18.730528746889938 | 2.9743589743589745 | 8.973838299262749 | -27.70436704615269 | 0.0 | 1.8205128205128205 | 17 | -624.7835306702389 | 1.1538461538461537 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 117 |
| 3 | -2.057 | -6.240 | 6.809 | -2.137 | 0.395 | 39 | 38.462 | 60.000 | 0.000 | 0.000 | 0.3 | 60 | 0.03 | 40 | 4 | 0.45 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -18.730528746889938 | 2.9743589743589745 | 8.973838299262749 | -27.70436704615269 | 0.0 | 1.8205128205128205 | 17 | -624.7835306702389 | 1.1538461538461537 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 117 |
| 4 | -2.057 | -6.240 | 6.809 | -2.137 | 0.395 | 39 | 38.462 | 60.000 | 0.000 | 0.000 | 0.5 | 60 | 0.03 | 40 | 4 | 0.55 | 0.005 | 0.015 | 0.25 | 0.012 | 0.5 | 65 | -18.730528746889938 | 2.9743589743589745 | 8.973838299262749 | -27.70436704615269 | 0.0 | 1.8205128205128205 | 17 | -624.7835306702389 | 1.1538461538461537 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 117 |
| 5 | -2.057 | -6.240 | 6.809 | -2.137 | 0.395 | 39 | 38.462 | 60.000 | 0.000 | 0.000 | 0.5 | 60 | 0.03 | 40 | 4 | 0.45 | 0.005 | 0.015 | 0.35 | 0.012 | 0.5 | 65 | -18.730528746889938 | 2.9743589743589745 | 8.973838299262749 | -27.70436704615269 | 0.0 | 1.8205128205128205 | 17 | -624.7835306702389 | 1.1538461538461537 | 10 | {"age": 32, "profit_protect": 7} | 0 | -162.94302299999995 | 7 | 32 | 40 | 117 |

## Per-top 诊断

解读:
- **exit_reason 分布**: session 退出原因占比. hard_stop / trend_risk / age 是主要 forced_exit 来源.
- **buy/sell fills/session**: 每 session 平均成交笔数. 太低 (<2) 说明 session 还没充分跑.
- **grid_close_count**: 网格平仓数 (GRID_SELL 配对). 0 说明 sell trigger 从未命中.
- **base_exit_loss vs grid_pnl**: 底仓退出的现金差 vs 网格交易盈亏. 哪一方主导亏损?
- **avg_realized vs avg_unrealized**: session 已实现 vs 浮盈差距, 揭示 'EXIT 时机' 问题.

### Top 1
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.730528746889938, avg_trades_per_session=2.9743589743589745, diag_avg_realized_session=8.973838299262749, diag_avg_unrealized_session=-27.70436704615269, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.8205128205128205, diag_grid_close_count=17, diag_grid_pnl_total=-624.7835306702389, diag_sell_fills_per_session=1.1538461538461537, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=117`
- score=-2.057 | total_return=-6.24% | dd=6.81% | sharpe=-2.137
- sessions=39 | session_win_rate=38.5% | profit_factor=0.39
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.82 | **sell_fills/session**=1.15
- **grid_close_count**=17 (sessions with grid_sell=10) | grid_close_win_rate=60.0%
- grid_pnl_total=$-624.78 | base_exit_loss=$+0.00
- avg_realized/session=$+8.97 | avg_unrealized/session=$-27.70

### Top 2
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.730528746889938, avg_trades_per_session=2.9743589743589745, diag_avg_realized_session=8.973838299262749, diag_avg_unrealized_session=-27.70436704615269, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.8205128205128205, diag_grid_close_count=17, diag_grid_pnl_total=-624.7835306702389, diag_sell_fills_per_session=1.1538461538461537, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=117`
- score=-2.057 | total_return=-6.24% | dd=6.81% | sharpe=-2.137
- sessions=39 | session_win_rate=38.5% | profit_factor=0.39
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.82 | **sell_fills/session**=1.15
- **grid_close_count**=17 (sessions with grid_sell=10) | grid_close_win_rate=60.0%
- grid_pnl_total=$-624.78 | base_exit_loss=$+0.00
- avg_realized/session=$+8.97 | avg_unrealized/session=$-27.70

### Top 3
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.3, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.730528746889938, avg_trades_per_session=2.9743589743589745, diag_avg_realized_session=8.973838299262749, diag_avg_unrealized_session=-27.70436704615269, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.8205128205128205, diag_grid_close_count=17, diag_grid_pnl_total=-624.7835306702389, diag_sell_fills_per_session=1.1538461538461537, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=117`
- score=-2.057 | total_return=-6.24% | dd=6.81% | sharpe=-2.137
- sessions=39 | session_win_rate=38.5% | profit_factor=0.39
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.82 | **sell_fills/session**=1.15
- **grid_close_count**=17 (sessions with grid_sell=10) | grid_close_win_rate=60.0%
- grid_pnl_total=$-624.78 | base_exit_loss=$+0.00
- avg_realized/session=$+8.97 | avg_unrealized/session=$-27.70

### Top 4
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.55, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.25, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.730528746889938, avg_trades_per_session=2.9743589743589745, diag_avg_realized_session=8.973838299262749, diag_avg_unrealized_session=-27.70436704615269, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.8205128205128205, diag_grid_close_count=17, diag_grid_pnl_total=-624.7835306702389, diag_sell_fills_per_session=1.1538461538461537, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=117`
- score=-2.057 | total_return=-6.24% | dd=6.81% | sharpe=-2.137
- sessions=39 | session_win_rate=38.5% | profit_factor=0.39
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.82 | **sell_fills/session**=1.15
- **grid_close_count**=17 (sessions with grid_sell=10) | grid_close_win_rate=60.0%
- grid_pnl_total=$-624.78 | base_exit_loss=$+0.00
- avg_realized/session=$+8.97 | avg_unrealized/session=$-27.70

### Top 5
- Params: `DEFENSIVE_MIN_REBOUND_ATR_TO_SELL=0.5, SESSION_ABSOLUTE_MAX_AGE_BARS=60, SESSION_HARD_STOP_PCT=0.03, SESSION_MAX_AGE_BARS=40, SESSION_MAX_BUY_LEVELS_NORMAL=4, SESSION_MAX_POSITION_VALUE_PCT_NORMAL=0.45, SESSION_MIN_PROFIT_TO_PROTECT_PCT=0.005, SESSION_SOFT_STOP_PCT=0.015, SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO=0.35, SESSION_STRONG_PROFIT_PCT=0.012, SESSION_TRAILING_GIVEBACK_RATIO=0.5, TREND_RISK_SCORE_DEFENSIVE=65, average_session_pnl=-18.730528746889938, avg_trades_per_session=2.9743589743589745, diag_avg_realized_session=8.973838299262749, diag_avg_unrealized_session=-27.70436704615269, diag_base_exit_loss=0.0, diag_buy_fills_per_session=1.8205128205128205, diag_grid_close_count=17, diag_grid_pnl_total=-624.7835306702389, diag_sell_fills_per_session=1.1538461538461537, diag_sessions_with_grid_sell=10, exit_reason_breakdown={"age": 32, "profit_protect": 7}, forced_exit_count=0, max_session_loss=-162.94302299999995, profit_protect_exit=7, timeout_exit=32, total_grid_sessions=40, total_trades=117`
- score=-2.057 | total_return=-6.24% | dd=6.81% | sharpe=-2.137
- sessions=39 | session_win_rate=38.5% | profit_factor=0.39
- **exit_reason**: age=32 (82%), profit_protect=7 (18%)
- forced_exit_ratio=0.0% | defensive_mode_ratio=0.0% | profit_protect_exit=7 | timeout_exit=32
- **buy_fills/session**=1.82 | **sell_fills/session**=1.15
- **grid_close_count**=17 (sessions with grid_sell=10) | grid_close_win_rate=60.0%
- grid_pnl_total=$-624.78 | base_exit_loss=$+0.00
- avg_realized/session=$+8.97 | avg_unrealized/session=$-27.70


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