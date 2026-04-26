# quant_factory Project Status

This file is automatically updated by Claude Code hooks.

## Last Updated

`2026-04-26T22:54:56Z`

## Current Branch

```text
main
```

## Last Commit

```text
4fdb801 Initial commit — Grid ETF v4 (UVXY 4h V49 baseline)
```

## Working Tree Status

```text
 M CLAUDE.md
 M PROJECT_STATUS.md
 M backtest.py
 M config.py
 M data/qqq.py
 M data/uvxy_4h.csv
 M main.py
 M pnl_tracker.py
 M risk_manager.py
 M simulated_executor.py
 M state_machine.py
```

## Changed Files

```text
CLAUDE.md
PROJECT_STATUS.md
backtest.py
config.py
data/qqq.py
data/uvxy_4h.csv
main.py
pnl_tracker.py
risk_manager.py
simulated_executor.py
state_machine.py
```

## Staged Files

```text
none
```

## Diff Stat

```text
 CLAUDE.md             |   5 +
 PROJECT_STATUS.md     | 304 +++++++++++++++-----------------------------------
 backtest.py           |  52 ++++++---
 config.py             |   4 +-
 data/qqq.py           |  16 +--
 data/uvxy_4h.csv      | 206 ++++++++++++++++++++++++++++++++++
 main.py               |   4 +-
 pnl_tracker.py        |  15 +--
 risk_manager.py       |  11 +-
 simulated_executor.py |   4 +-
 state_machine.py      |  22 ++--
 11 files changed, 376 insertions(+), 267 deletions(-)
```

## Notes

- `CLAUDE.md` defines project rules for Claude Code.
- `logs/claude_sessions/` stores per-session development logs.
- Git is the main protection against lost progress.
- Hooks update this file after Claude Code edits.
- Do not rely on this file as a substitute for Git commits.

## Manual Next Steps

- Review changed files.
- Run relevant tests or syntax checks if needed.
- Commit stable checkpoints manually.
