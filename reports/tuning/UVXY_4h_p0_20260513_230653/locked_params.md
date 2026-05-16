# Locked Parameters Snapshot

P3 / P4 参数在调参时**不参与搜索**, 仅作为系统约束展示.
调整这些值的影响**不是策略优化**, 而是改变系统假设或运行成本.

## P3 — 系统级 / 真实费用

| 参数 | 当前值 | 说明 |
|---|---:|---|
| HARD_STOP_LOSS_PCT | 0.2 | locked |
| MAX_DAILY_LOSS_PCT | 0.05 | locked |
| MAX_POSITION_VALUE_PCT | 0.95 | locked |
| IBKR_COMMISSION_MIN | 0.35 | locked |
| IBKR_COMMISSION_PER_SHARE | 0.0035 | locked |
| SEC_FEE_RATE | 2.78e-05 | locked |
| TAF_FEE_PER_SHARE | 0.000166 | locked |
| TAF_FEE_MIN | 0.01 | locked |
| TAF_FEE_MAX | 8.3 | locked |
| BT_MARKET_SLIP_BPS | 5.0 | locked |
| BT_LIMIT_SLIP_BPS | 1.0 | locked |
| BT_LIMIT_FILL_PROB_TOUCH | 0.6 | locked |
| BT_LIMIT_FILL_PROB_CROSS | 1.0 | locked |
| BT_GAP_FILL_PROB | 0.95 | locked |
| TRADING_START_HOUR | 10 | locked |
| TRADING_START_MINUTE | 30 | locked |
| TRADING_END_HOUR | 15 | locked |
| TRADING_END_MINUTE | 30 | locked |
| FRIDAY_CUTOFF_HOUR | 15 | locked |
| FRIDAY_CUTOFF_MINUTE | 0 | locked |
| STARTUP_PRICE_TIMEOUT_SEC | 15.0 | locked |
| PRICE_TIMEOUT_SEC | 15.0 | locked |
| PRICE_RETRY_COUNT | 2 | locked |
| IBKR_HIST_PREV_CLOSE_DAYS | 5 | locked |

## P4 — 频率 / 调度 / API 压力

| 参数 | 当前值 | 说明 |
|---|---:|---|
| ACTIVE_CHECK_INTERVAL_SEC | 60 | locked |
| WAITING_ENTRY_CHECK_INTERVAL_SEC | 300 | locked |
| ENTRY_MAX_WAIT_BARS | 1.5 | locked |
| ENTRY_TIMEOUT_EPSILON_BARS | 1e-06 | locked |
| ENTRY_EXECUTION_MAX_FAILURES | 2 | locked |