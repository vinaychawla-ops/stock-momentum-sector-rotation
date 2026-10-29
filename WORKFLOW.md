# US Operations Guide — Yahoo Finance Paper (NYSE)

Daily workflow is paper-only. No IBKR TWS required.

## Daily — After NYSE Close (16:00 ET)

```bash
# 1. Snapshot mark-to-market (yfinance close)
python main.py snapshot
# → data/snapshots/YYYY-MM-DD.json

# 2. (Optional) check signal
python main.py signal

# 3. If it's the monthly rebalance (1st trading day) or you want to trade:
python main.py rebalance
python main.py execute          # dry-run preview
python main.py execute --live   # CONFIRM → simulated fills → data/executions/YYYY-MM-DD.csv + updated snapshot
```

With `fill_model: next_open` (default) the run above leaves trades **pending**,
because the next open does not exist yet. Exit code `2` means "come back after
the session opens":

```bash
# next morning, after 09:30 ET
python main.py execute --live   # fills the pending trades at that session's open
```

## Monthly Rebalance (1st Trading Day)

```bash
python main.py signal
python main.py rebalance
python main.py execute --live   # pending after the close; re-run after the next open
python main.py snapshot         # already done by broker post-fill, but you can re-mark at close
```

## Reports

```bash
mkdir -p data/annotations/monthly
echo "## Aug 2026 — tech-led momentum held" > data/annotations/monthly/2026-08.md
python main.py report 2026-08
open reports/monthly/2026-08-report.pdf
```

## Scheduler (hands-off)

```bash
python main.py scheduler
# Snapshot 16:05 ET on every trading day (XNYS calendar)
# Execute 16:10 ET on the first trading day of the month (rebalance_cadence: monthly)
# Weekly report Sunday 21:00 ET
# Manual: python main.py run-job snapshot
# Health: http://localhost:8080/
```

## Dashboard

```bash
python main.py dashboard --port 5050
# http://localhost:5050 — equity curve, positions, momentum ranks, paper banner
# http://localhost:5050/signals — audit history
```

## Kill Switch

```bash
python main.py kill-switch status
python main.py kill-switch activate "Volatility halt"
python main.py kill-switch deactivate
```

## Troubleshooting

- `No snapshot`: run `python main.py seed --cash 100000` on fresh workspace.
- `No price data`: yfinance rate-limit or weekend — retry; batch downloads are resilient.
- `Snapshot is N hours old`: run `python main.py snapshot`.
- `Dashboard port busy`: use `--port 5051`.

See README for universe/fill_model config.
