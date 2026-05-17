# Telegram Read-Only Sidecar — Operator Runbook

> Companion to spec `docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md` §5 and plan `docs/superpowers/plans/2026-05-17-telegram-sidecar.md`.

## 1. What this is

A separate Docker service (`telegram-bot`) running alongside `uvxy-grid` and `ib-gateway`. It reads — never writes — your trading state and exposes 10 commands plus 5 push-notification channels.

The sidecar **cannot** place or cancel orders, change parameters, or otherwise affect the trading system. This is enforced in code (`telegram_bot/readers/ibkr_ro.py` has an import-time assertion that rejects any write-API method).

## 2. Token rotation (first step before deploy)

The token used during brainstorming was shared in chat history. Rotate it before going live:

1. In Telegram, talk to [@BotFather](https://t.me/BotFather).
2. `/mybots` → select your bot → **API Token** → **Revoke current token**.
3. Copy the new token.
4. Put the new token into `.env` on the Synology host **only**. Do not commit. Do not paste into a code editor that auto-syncs.

## 3. Deployment

Prereq: `uvxy-grid` and `ib-gateway` already running and healthy on Synology.

```
ssh synology
cd /volume2/docker/uvxy_grid

# Populate .env
cat >> .env <<'EOF'
TELEGRAM_BOT_TOKEN=<NEW_TOKEN_FROM_BOTFATHER>
TELEGRAM_CHAT_ID=<YOUR_CHAT_ID>
EOF
chmod 600 .env

# Build new sidecar
docker compose build telegram-bot

# Smoke test inside container (no network calls)
docker compose run --rm telegram-bot python -m telegram_bot.bot --smoke

# Bring sidecar up
docker compose up -d telegram-bot
docker compose logs -f telegram-bot
```

In a Telegram chat with your bot: `/help`. You should see all 10 commands listed.

## 4. Commands

| Command | What it does |
|---|---|
| `/status` | Per-sub-bot current state + IBKR connection + heartbeat age |
| `/positions` | Current positions from IBKR (shares / avg / mkt / value / unrealized) |
| `/pnl` | Realized + unrealized PnL (IBKR authoritative, SQLite fallback) |
| `/grid` | Per-sub-bot grid: center, spacing, closest levels |
| `/orders` | Open orders from IBKR (GTC limits) |
| `/risk` | Latest `risk_state` baseline + last N `risk_events` (no rule recompute) |
| `/report` | Sends the newest `weekly_*.html` as a document |
| `/logs N` | Last N lines of `grid_trader.log` (capped, account-id masked) |
| `/health` | Full self-check across IBKR / SQLite / grid / log |
| `/help` | Lists commands |

## 5. Push notifications

The sidecar pushes a message when:
1. A new state transition appears in any `state_transitions` table.
2. A new risk event appears in `risk_events`.
3. A line matching `ERROR`, `Traceback`, `IBKR.*(disconnect|reconnect)`, or `bot.*(started|shutdown)` appears in the log.
4. The newest event across all event tables exceeds `TG_HEARTBEAT_STALE_MIN` minutes during market hours (and a recover message when it freshens up again).

Cold-start behavior: on each restart, the sidecar records the current MAX(id) / log byte offset and emits **no** retro-notifications.

## 6. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Container exits immediately | Missing `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` | Set in `.env`, restart |
| `IBKR_CLIENT_ID must differ from 1` | Default not overridden | `.env` `IBKR_CLIENT_ID=99` |
| Sidecar runs but no response in chat | Wrong chat_id | Confirm chat_id by sending `/help` and check container log for `unauthorized chat_id=...` |
| `/positions` empty | Real account is flat OR sidecar IBKR conn dropped | Run `/health` |
| Bot replies "command_failed:" | Caught exception in a handler | Check container log for the traceback; commands isolated so other commands still work |

## 7. Stopping the sidecar

```
docker compose stop telegram-bot
```
The main trading bot is unaffected.

## 8. Out of scope (by design)

- No order placement / cancellation
- No parameter changes
- Multi-user / group chat
- Web dashboard
- Pushing notifications by the main bot
