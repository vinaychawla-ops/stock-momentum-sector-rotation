# US Momentum — Paper Trading (Yahoo Finance · NYSE)

Systematic **12-1 momentum sector rotation** across US SPDR ETFs, running entirely on **Yahoo Finance** with a deterministic paper ledger. No IBKR, no TWS, no broker credentials.

This is a port of [`amr-coding1/live-trading-jan2026`](https://github.com/amr-coding1/live-trading-jan2026) (UK UCITS + `ib_insync`) to US markets, USD, and the NYSE calendar. Fills are simulated and prices are delayed — nothing here touches a real brokerage account.

## Universe

11 SPDR GICS sector ETFs:

| Ticker | Sector | Ticker | Sector |
|---|---|---|---|
| XLK | Technology | XLB | Materials |
| XLF | Financials | XLU | Utilities |
| XLE | Energy | XLC | Communication Services |
| XLV | Health Care | XLRE | Real Estate |
| XLY | Consumer Discretionary | | |
| XLP | Consumer Staples | | |
| XLI | Industrials | | |

`universe.mode` selects the basket and **wins over `universe.tickers`** for the two named modes:

```yaml
universe:
  mode: classic_9   # full_11 (default) | classic_9 | custom
  tickers: [...]    # used only when mode: custom
```

`classic_9` drops `XLC`/`XLRE` for parity with the original 9-ETF UK set.

## How it works

1. **Signal** — month-end resampled adjusted closes, then 12-1 cumulative momentum (12 months, skipping the most recent). One batch `yf.download` for the whole universe. A symbol with a shorter history than the window is dropped with a warning instead of failing the batch.
2. **Selection** — rank descending, take the top 3 that clear `MIN_MOMENTUM_THRESHOLD = -20%`, equal-weight each. If only 2 qualify, each gets 50% and the rest stays in cash.
3. **Sizing** — `PositionSizer` converts weight deltas into whole shares under **one shared cash budget**: sells are sized first and their proceeds fund the buys, commission is reserved, and buys are clipped rather than overdrawing the account.
4. **Risk** — kill switch, per-position cap (`0.35`), aggregate turnover cap, and a 48-hour staleness guard on the snapshot the trade list is built from. A missing price for a target symbol aborts the run instead of trading a partial portfolio.
5. **Fills** — `PaperBroker` applies `reference_price × (1 ± slippage_bps/1e4)`, writes the executions CSV, updates the ledger, and re-marks the portfolio to market.

## Fill models

`data.fill_model` (or `execution.fill_model`) picks the reference price:

| Model | Reference | When it fills |
|---|---|---|
| `close` | Latest available close | Immediately |
| `next_open` (default) | Open of the first session **after** the snapshot's `asof_close` | On the next session. Before that session opens, trades are reported **pending** and nothing is written. |

Running `execute --live` after the close on day D therefore produces pending trades; re-running it after the next open fills them at that open. Pending behavior is explicit:

```
$ python main.py execute --live
...
Pending (waiting for the next open):
 XLK: not filled — re-run 'execute --live' after the session opens
3 fill(s) pending the next open: XLK, XLF, XLE
```

The command exits **2** for pending, **1** for a failure or rejection, **0** on success, so a scheduler or wrapper can tell the difference.

## Quick start

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml

python main.py seed --cash 100000     # first run only — creates an all-cash snapshot
python main.py signal                 # ranked momentum + target weights
python main.py rebalance              # current vs target, trade list
python main.py execute                # dry run (no fills, writes an audit log)
python main.py execute --live         # type CONFIRM → simulated fills
python main.py snapshot               # mark-to-market at the yfinance close
python main.py stats                  # return, vol, Sharpe, drawdown, win rate
```

## CLI reference

| Command | Purpose |
|---|---|
| `seed [--cash N] [--force]` | Create the initial all-cash snapshot. No-op if one exists without `--force`. |
| `snapshot` | Mark the portfolio to market at the latest close. |
| `signal [--top-n 3]` | Print the 12-1 ranking and target weights. |
| `rebalance [--top-n 3] [--threshold 0.02]` | Print the trade list for the current snapshot. |
| `execute [--live]` | Run the 7-step pipeline. Exits 2 when fills are pending the next open. |
| `stats [START] [END]` | Performance report. Dates are positional `YYYY-MM-DD`. |
| `slippage [START] [END] [--threshold 10]` | Modeled slippage summary and outliers. |
| `report YYYY-MM` | Monthly PDF into `reports/monthly/`. |
| `weekly-report [YYYY-Www]` | Weekly PDF, defaults to the current ISO week. |
| `annotate [TRADE_ID] [--pre\|--post\|--list]` | Interactive thesis notes. |
| `dashboard [--host H] [--port 5050]` | Flask UI at `/` and `/signals`. |
| `scheduler [--health-port 8080]` | Exchange-time scheduler with a JSON health endpoint. |
| `run-job {snapshot\|execute\|weekly_report\|monthly_report}` | Run one scheduler job immediately. |
| `kill-switch {status\|activate\|deactivate} ["reason"]` | File-based trading halt at `data/.kill_switch`. |
| `pull` | Deprecated. Prints a notice, then runs `snapshot`. |

## Configuration

See `config/config.example.yaml`. Keys that drive behaviour:

| Key | Default | Effect |
|---|---|---|
| `timezone` | `America/New_York` | Wall clock for the scheduler. Calendar is always XNYS. |
| `universe.mode` / `universe.tickers` | `full_11` | Named basket wins; `custom` uses the ticker list. |
| `paper.initial_cash`, `paper.currency` | `100000`, `USD` | Seed defaults. |
| `data.fill_model` | `next_open` | `next_open` or `close`; anything else raises. |
| `data.slippage_bps` | `5` | Modeled per-fill slippage. |
| `data.commission_per_share` | `0.00` | Per-share commission, reserved in sizing. |
| `position_sizing.top_n` | `3` | Number of sectors held. |
| `position_sizing.min_trade_threshold` | `0.02` | Weight delta that triggers a trade. |
| `position_sizing.min_trade_shares` | `1` | Share floor per trade. |
| `position_sizing.min_trade_value` | `100` | Dollar floor per trade. |
| `risk_limits.max_position_pct` | `0.35` | Per-position cap. Must be ≥ `1/top_n` or every rebalance fails validation. |
| `risk_limits.max_turnover_pct` | `1.00` | Aggregate turnover cap per run. |
| `scheduler.snapshot_time` / `execute_time` | `16:05` / `16:10` | Exchange wall-clock times. |
| `scheduler.rebalance_cadence` | `monthly` | `monthly` = first trading day, or `weekly` / `daily` / `off`. |
| `signals.retention_days` | `365` | Prune signal logs older than this; `0` keeps everything. |
| `logging.*` / `paths.logs` | `INFO`, 10 MB | Console + rotating `logs/momentum.log`. |

## Scheduler

```bash
python main.py scheduler
# 16:05 snapshot on every trading day
# 16:10 execute on the first trading day of the month (cadence: monthly)
# Sunday 21:00 weekly report
# Health: http://localhost:8080/  → {status, et_now, is_trading_day, calendar, rebalance_cadence}
```

One 30-second tick compares the exchange wall clock against the configured times, and each `(date, time, job)` fires at most once. The scheduler only writes fills when `execution.mode: live`; the default `dry_run` keeps it read-only. If `execute` leaves fills pending the next open, the scheduler logs a warning telling you to re-run after the session opens.

## Dashboard & reports

```bash
python main.py dashboard --port 5050
# http://localhost:5050        equity curve, positions, momentum ranks, paper banner
# http://localhost:5050/signals run history, kill-switch state

python main.py report 2026-08
python main.py weekly-report
```

Signal logs are one file per run (`YYYY-MM-DD_HHMMSS_<mode>.json`), so a dry run and a live run on the same day both remain. PDFs go to `reports/monthly/` with USD formatting and a simulated-fills disclaimer.

## Project layout

```
main.py                  CLI entry point
src/broker/              yfinance gateway, paper broker (fills, cash guard), broker protocol
src/portfolio/           cash + average-cost ledger, atomic snapshots
src/signals/             momentum, rebalance
src/execution/           engine, sizer, risk, audit logger
src/logging_setup.py     console + rotating file logging
src/performance.py       metrics from snapshots + executions
src/{dashboard,export,scheduler,slippage_analyzer,annotations,notifications}.py
tests/                   36 pytest tests
docs/                    STRATEGY, ARCHITECTURE, OPERATIONS
data/                    snapshots, executions, signals, annotations (gitignored payloads)
logs/                    momentum.log (rotating, gitignored)
```

## Testing

```bash
pytest -q          # 36 passed
python test_all.py # same suite via subprocess
```

Covered: momentum math and universe resolution, short-history handling, the ledger (including cash clipping), broker dry-run / `close` / `next_open` / pending / rejection paths, position sizing under one cash budget, rebalance budgeting, signal-log retention, performance metrics, and the XNYS trading-day and first-trading-day checks. Not covered by automated tests: the Flask dashboard, PDF export, SMTP notifications, and the scheduler loop's wall-clock ticks.

## Known limits

- **Yahoo Finance is unofficial and delayed (~15 minutes).** Good enough for an end-of-day signal, not for intraday use.
- **Paper only.** Fills are simulated at a reference price ± modeled slippage. There is no broker, no order book, no partial fills, and `slippage_bps` records the configured assumption rather than an observed spread.
- **Long-only, whole shares, one currency.** No shorting, no fractional shares, no FX.
- **No benchmark attribution.** `stats` reports absolute return, vol, Sharpe (rf = 0), drawdown and win rate — not alpha or beta versus SPY.
- **XNYS only.** `pandas_market_calendars` drives trading days; other exchanges are out of scope.
- **Signal log retention is file-based**, not a database: pruning deletes by filename date.

## Disclaimer

Paper trading on Yahoo Finance. Prices are unofficial, delayed, and adjusted; fills are simulated and not broker-verified. Metrics, slippage, and PDF reports describe a simulation, not realised trading. Not financial advice.
