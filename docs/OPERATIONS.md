# Operations — US NYSE Paper

## Setup
```
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
# edit universe.mode / fill_model / slippage_bps if needed
python main.py seed --cash 100000
```

## Daily
After the NYSE close (16:00 ET):
```
python main.py snapshot          # mark-to-market at yfinance close
python main.py signal            # check ranks
python main.py rebalance         # preview trades (cash-budgeted)
python main.py execute           # dry-run
python main.py execute --live    # CONFIRM → paper fills
python main.py stats             # Sharpe etc.
```

## Pending fills (next_open model)
With `fill_model: next_open` (default), a run after the close cannot price the
next open. It writes nothing and exits **2**:

```
Pending (waiting for the next open):
 XLK: not filled — re-run 'execute --live' after the session opens
2 fill(s) pending the next open: XLK, XLF
```

Re-run `python main.py execute --live` after 09:30 ET the next session. Those
trades then fill at that session's open plus slippage. Exit codes: `0` success,
`1` failure or rejection, `2` pending.

## Scheduler
```
python main.py scheduler         # exchange-time tick; health http://localhost:8080/
python main.py run-job snapshot
python main.py run-job execute
```
Default cadence is `monthly`: snapshot every trading day at 16:05, execute at
16:10 on the first trading day of the month, weekly report Sunday 21:00.
Set `scheduler.rebalance_cadence` to `weekly`, `daily`, or `off` if needed.
Trading days come from `pandas_market_calendars` XNYS; wall-clock times from
`timezone`. The scheduler writes fills only when `execution.mode: live`.

## Dashboard & Reports
```
python main.py dashboard --port 5050   # http://localhost:5050 and /signals
python main.py report 2026-08          # reports/monthly/2026-08-report.pdf
python main.py weekly-report           # current week
```

## Kill Switch
```
python main.py kill-switch status
python main.py kill-switch activate "halt reason"
python main.py kill-switch deactivate
```

## Logs
`logs/momentum.log` rotates at 10 MB with 5 backups (`logging.*` in config).
Signal audit files live in `data/signals/` (one per run) and are pruned after
`signals.retention_days` (365 by default).

## Notes
- No TWS/Gateway: `Dockerfile` has no IB ports; `docker-compose.yml` runs scheduler + dashboard.
- `pull` is deprecated → `snapshot`.
- yfinance is rate-limited: downloads are batched with 2 retries. If a target symbol has no price, `execute` aborts rather than trading a partial portfolio.
- Cash cannot go negative: the sizer budgets it, the broker clips buys, and the ledger clips again.
