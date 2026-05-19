#!/bin/bash
# check_today.sh — 当日运行 / 收益 / 风险 / 状态机一键巡检
#
# 适配当前版本 (2026-05 起):
#   - 每标的独立 DB:        runtime/trades_<symbol>.db   (bot_factory: trades_{sym}.db)
#   - 账户级共享 DB:        runtime/account.db          (AccountRiskManager)
#   - 容器:                  ib-gateway / uvxy-grid / telegram-bot
#   - 默认单标的:            UVXY  (main.py DEFAULT_SYMBOLS)
#
# 出错容忍: 单个表 / DB 不存在不应中断后续 section, 因此关闭 set -e,
# 改为对每个 sqlite3 调用单独 || true.

set -u

COMPOSE_DIR="/volume2/docker/docker_ibgateway"
APP_DIR="/volume2/docker/uvxy_grid"
RUNTIME_DIR="$APP_DIR/runtime"
ACCOUNT_DB="$RUNTIME_DIR/account.db"
LOG="$RUNTIME_DIR/grid_trader.log"

TODAY="$(date +%F)"

sql() {
  # sql <db_path> <query>   — 容错执行, 表 / DB 缺失时打印一行提示而不是中断脚本.
  local db="$1"; shift
  if [ ! -f "$db" ]; then
    echo "  (skip: $db 不存在)"
    return 0
  fi
  sqlite3 -header -column "$db" "$@" 2>&1 || true
}

echo "=== Docker containers ==="
cd "$COMPOSE_DIR"
sudo docker compose ps -a

echo ""
echo "=== Recent uvxy-grid docker logs (30m) ==="
sudo docker compose logs --since=30m uvxy-grid || true

echo ""
echo "=== Recent telegram-bot docker logs (30m) ==="
sudo docker compose logs --since=30m telegram-bot || true

echo ""
echo "=== Runtime log tail (grid_trader.log) ==="
if [ -f "$LOG" ]; then
  tail -n 120 "$LOG"
else
  echo "  (skip: $LOG 不存在)"
fi

# ─────────────────────────────────────────────────────────────
#  账户级 (account.db) — AccountRiskManager 共享状态
# ─────────────────────────────────────────────────────────────

echo ""
echo "=== [account.db] Account risk state (latched flags) ==="
sql "$ACCOUNT_DB" "
SELECT
  hard_stop_triggered,
  daily_loss_triggered,
  last_reset_date,
  updated_at
FROM account_risk_state
WHERE id = 1;
"

echo ""
echo "=== [account.db] Account-level realized PnL today (by symbol) ==="
sql "$ACCOUNT_DB" "
SELECT
  symbol,
  COUNT(*)        AS closes,
  ROUND(SUM(net_pnl), 2) AS net_pnl_today
FROM account_trade_pnl
WHERE date = '$TODAY'
GROUP BY symbol
ORDER BY symbol;
"

echo ""
echo "=== [account.db] Account-level realized PnL today (rows, last 30) ==="
sql "$ACCOUNT_DB" "
SELECT
  timestamp,
  symbol,
  net_pnl,
  note
FROM account_trade_pnl
WHERE date = '$TODAY'
ORDER BY timestamp DESC
LIMIT 30;
"

echo ""
echo "=== [account.db] Account risk events today ==="
sql "$ACCOUNT_DB" "
SELECT
  timestamp,
  event_type,
  details,
  action_taken
FROM account_risk_events
WHERE date(timestamp) = '$TODAY'
ORDER BY timestamp DESC
LIMIT 30;
"

# ─────────────────────────────────────────────────────────────
#  每标的 (trades_<sym>.db) — 状态机 / 交易 / PnL / 评估 / 转换 / recenter / 风险
# ─────────────────────────────────────────────────────────────

shopt -s nullglob
PER_SYM_DBS=("$RUNTIME_DIR"/trades_*.db)
# 兼容旧单标的部署: 如果还存在 trades.db, 也一并扫.
if [ -f "$RUNTIME_DIR/trades.db" ]; then
  PER_SYM_DBS+=("$RUNTIME_DIR/trades.db")
fi

if [ ${#PER_SYM_DBS[@]} -eq 0 ]; then
  echo ""
  echo "=== Per-symbol DBs ==="
  echo "  (no trades_*.db found in $RUNTIME_DIR — 系统可能还未启动过)"
fi

for DB in "${PER_SYM_DBS[@]}"; do
  base="$(basename "$DB" .db)"
  # trades_uvxy → uvxy ; trades → (legacy single-symbol)
  case "$base" in
    trades_*)  SYM="${base#trades_}" ;;
    trades)    SYM="legacy" ;;
    *)         SYM="$base" ;;
  esac

  echo ""
  echo "================================================================"
  echo "  Symbol: ${SYM^^}     ($DB)"
  echo "================================================================"

  echo ""
  echo "--- [${SYM^^}] State machine current ---"
  sql "$DB" "
SELECT
  current_state,
  state_entered_at,
  last_evaluation_time,
  entry_window_started_at,
  grid_active_since,
  last_recenter_at,
  exit_initiated_at,
  exit_reason,
  total_grid_sessions,
  total_recenters,
  total_exits,
  updated_at
FROM state_machine_state;
"

  echo ""
  echo "--- [${SYM^^}] Trades today ---"
  sql "$DB" "
SELECT
  id,
  timestamp,
  action,
  symbol,
  quantity,
  price,
  grid_level,
  commission,
  pnl,
  note
FROM trades
WHERE date(timestamp) = '$TODAY'
ORDER BY timestamp DESC;
"

  echo ""
  echo "--- [${SYM^^}] PnL closes today ---"
  sql "$DB" "
SELECT
  sell_timestamp,
  sell_quantity,
  sell_price,
  gross_pnl,
  net_pnl,
  is_win,
  unmatched_quantity
FROM pnl_closes
WHERE date(sell_timestamp) = '$TODAY'
ORDER BY sell_timestamp DESC;
"

  echo ""
  echo "--- [${SYM^^}] Open FIFO lots (pnl_fifo_queue snapshot) ---"
  sql "$DB" "
SELECT
  id,
  timestamp,
  quantity,
  price,
  commission,
  level_index,
  order_id
FROM pnl_fifo_queue
ORDER BY id;
"

  echo ""
  echo "--- [${SYM^^}] Entry evaluations today (last 20) ---"
  sql "$DB" "
SELECT
  timestamp,
  price,
  adx,
  atr_pct,
  bb_width_pct,
  ema,
  allow_entry,
  rejection_reasons
FROM entry_evaluations
WHERE date(timestamp) = '$TODAY'
ORDER BY timestamp DESC
LIMIT 20;
"

  echo ""
  echo "--- [${SYM^^}] State transitions today (last 30) ---"
  sql "$DB" "
SELECT
  timestamp,
  from_state,
  to_state,
  reason
FROM state_transitions
WHERE date(timestamp) = '$TODAY'
ORDER BY timestamp DESC
LIMIT 30;
"

  echo ""
  echo "--- [${SYM^^}] Grid recenters today ---"
  sql "$DB" "
SELECT
  timestamp,
  old_center,
  new_center,
  old_spacing_pct,
  new_spacing_pct,
  atr_value,
  reason
FROM grid_recenters
WHERE date(timestamp) = '$TODAY'
ORDER BY timestamp DESC;
"

  echo ""
  echo "--- [${SYM^^}] Risk events today (per-symbol risk_events) ---"
  sql "$DB" "
SELECT
  timestamp,
  event_type,
  details,
  action_taken
FROM risk_events
WHERE date(timestamp) = '$TODAY'
ORDER BY timestamp DESC
LIMIT 30;
"

  echo ""
  echo "--- [${SYM^^}] Risk state (last prev_close snapshot) ---"
  sql "$DB" "
SELECT
  id,
  timestamp,
  prev_close,
  note
FROM risk_state
ORDER BY id DESC
LIMIT 3;
"

  echo ""
  echo "--- [${SYM^^}] Daily snapshots (last 5) ---"
  sql "$DB" "
SELECT
  date,
  state,
  total_equity,
  position_shares,
  position_value,
  cash,
  unrealized_pnl,
  realized_pnl_today,
  grid_center,
  note
FROM daily_snapshots
ORDER BY date DESC
LIMIT 5;
"
done

echo ""
echo "=== done ==="
