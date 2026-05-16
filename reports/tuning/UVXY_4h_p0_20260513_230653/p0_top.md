# Tactical Tune — Profile **p0**

- Symbol: `UVXY` | Interval: `4h` | Years: `1.0`
- Total combinations explored: **324**
- Rows passing filters (sessions ≥ 5, dd < 50%): **5**
- Generated at: 2026-05-13T23:10:35.504197

## Scoring

score = annual_ret + 0.5×sharpe + 1.5×(profit_factor−1) − drawdown − forced_exit_ratio
  - session_count < 20 折半 (鼓励样本量)

## Top Results

| Rank | score | total_return_pct | max_drawdown_pct | sharpe | profit_factor | session_count | session_win_rate | grid_close_win_rate | forced_exit_ratio | defensive_mode_ratio | SESSION_ABSOLUTE_MAX_AGE_BARS | SESSION_HARD_STOP_PCT | SESSION_MAX_AGE_BARS | SESSION_SOFT_STOP_PCT | SESSION_TRAILING_GIVEBACK_RATIO | TREND_RISK_SCORE_DEFENSIVE | average_session_pnl | avg_trades_per_session | forced_exit_count | max_session_loss | profit_protect_exit | timeout_exit | total_grid_sessions | total_trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | -0.761 | -6.643 | 19.447 | -0.547 | 0.564 | 12 | 33.333 | 0.000 | 33.333 | 0.000 | 40 | 0.03 | 24 | 0.015 | 0.5 | 70 | -66.63759883333306 | 1.5 | 4 | -388.0534400000002 | 0 | 1 | 13 | 32 |
| 2 | -0.761 | -6.643 | 19.447 | -0.547 | 0.564 | 12 | 33.333 | 0.000 | 33.333 | 0.000 | 40 | 0.03 | 24 | 0.015 | 0.5 | 65 | -66.63759883333306 | 1.5 | 4 | -388.0534400000002 | 0 | 1 | 13 | 32 |
| 3 | -0.761 | -6.643 | 19.447 | -0.547 | 0.564 | 12 | 33.333 | 0.000 | 33.333 | 0.000 | 40 | 0.03 | 24 | 0.015 | 0.5 | 75 | -66.63759883333306 | 1.5 | 4 | -388.0534400000002 | 0 | 1 | 13 | 32 |
| 4 | -0.761 | -6.643 | 19.447 | -0.547 | 0.564 | 12 | 33.333 | 0.000 | 33.333 | 0.000 | 40 | 0.03 | 30 | 0.015 | 0.5 | 65 | -66.63759883333306 | 1.5 | 4 | -388.0534400000002 | 0 | 1 | 13 | 32 |
| 5 | -0.761 | -6.643 | 19.447 | -0.547 | 0.564 | 12 | 33.333 | 0.000 | 33.333 | 0.000 | 40 | 0.03 | 30 | 0.015 | 0.5 | 70 | -66.63759883333306 | 1.5 | 4 | -388.0534400000002 | 0 | 1 | 13 | 32 |

## 下一步建议

1. 不要直接把 top-1 参数写回 config.py — 先 walk-forward 验证 (--walk-forward)
2. P0 跑完后, 在 top 5 附近跑 P1; P0+P1 锁定后再跑 P2
3. 检查每行的 forced_exit_ratio — 过高 (>50%) 说明参数仍偏紧
4. profit_factor < 1.0 即使 total_return 为正, 也说明仰仗少数大盈利, 不稳健