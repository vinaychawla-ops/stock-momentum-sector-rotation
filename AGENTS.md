# AGENTS.md — US Momentum Paper (Yahoo Finance · NYSE)

> Port of `amr-coding1/live-trading-jan2026` (UK UCITS + IBKR) to US SPDR sectors on Yahoo Finance only — no `ib_insync`, no TWS, no sockets, no broker credentials.

This file is the map agents need before editing. It records what the code does now, including the invariants that keep the paper ledger honest. Read §7 before changing fills, sizing, or the scheduler.

## 1. Project Overview

- **Strategy:** 12-1 momentum (12-month cumulative return excluding most recent month), top-3 equal-weight, monthly rebalance. Cash buffer when fewer than 3 sectors pass `MIN_MOMENTUM_THRESHOLD = -0.20` (`src/signals/momentum.py`). If 2 pass, each gets 50%.
- **Universe:** 11 SPDR GICS ETFs — `XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLC, XLRE`. `FULL_11` default, `CLASSIC_9` drops `XLC`/`XLRE`.
- **Universe precedence:** `universe.mode` wins for `full_11`/`classic_9`; `universe.tickers` applies only when `mode` is missing or `custom`. Editing `mode` alone is now enough to switch baskets — don't reintroduce ticker-first resolution.
- **Market & time:** NYSE. `zoneinfo(config.timezone)` (default `America/New_York`), `XNYS` calendar. Scheduler: `16:05` snapshot on trading days; `16:10` execute on `scheduler.rebalance_cadence` (`monthly` = first trading day by default, or `weekly`/`daily`/`off`); weekly PDF Sunday `21:00`.
- **Data source:** yfinance only, `auto_adjust=True`, batch `yf.download`. Fills are **simulated**, not broker-verified.
- **Fill models:** `next_open` (default) fills at the open of the first session after the snapshot's `asof_close`; `close` fills at the latest close. Unknown values raise at construction.
- **Currency:** USD `$`.
- **Runtime:** verified on Python 3.14.4; `Dockerfile` pins `python:3.11-slim`. Keep code 3.11-compatible.
- **VCS:** `git` repository, branch `main`. Commits are yours to make unless the user asks otherwise.

## 2. Directory Structure

```
.
├── main.py                      # argparse CLI — one cmd_<name> function per subcommand
├── config/
│   ├── config.example.yaml      # template (commit this)
│   └── config.yaml              # local, gitignored, same shape as the example
├── src/
│   ├── broker/
│   │   ├── yfinance_provider.py # ONLY yfinance gateway: download_prices, get_current_prices,
│   │   │                        #   get_ohlc, get_latest_session_ohlc, month_floor, health_check
│   │   ├── paper_broker.py      # fills, cash guard, atomic executions CSV, post-fill snapshot
│   │   └── base.py              # Broker Protocol + ExecutionResult dataclass
│   ├── portfolio/
│   │   └── paper_portfolio.py   # cash + average-cost ledger, atomic snapshots, clipping apply_fills
│   ├── signals/
│   │   ├── momentum.py          # FULL_11/CLASSIC_9, universe modes, 12-1 signal, text report
│   │   └── rebalance.py         # snapshot vs targets → trade list, cash-budgeted preview
│   ├── execution/
│   │   ├── engine.py            # 7-step pipeline, 48h staleness guard, pending propagation
│   │   ├── position_sizer.py    # weight delta → shares under one shared cash budget
│   │   ├── risk_manager.py      # kill switch, position cap, turnover cap
│   │   └── signal_logger.py     # one JSON per run + retention pruning
│   ├── logging_setup.py         # console + rotating logs/momentum.log
│   ├── performance.py           # Sharpe/DD/win rate/profit factor from snapshots + executions
│   ├── slippage_analyzer.py     # modeled bps summary (not broker-observed)
│   ├── annotations.py           # interactive pre/post thesis notes
│   ├── export.py                # ReportLab monthly + weekly PDF (USD, paper disclaimer)
│   ├── dashboard.py             # Flask create_app(): `/` and `/signals`, CSP headers
│   ├── scheduler.py             # exchange-time tick, dedupe, cadence, `:8080` health
│   └── notifications.py         # optional SMTP summary (`email.enabled: false`)
├── templates/dashboard.html, templates/signals.html
├── tests/                       # 36 tests — see §6
├── test_all.py                  # wrapper: `python -m pytest tests/ -v`
├── data/                        # snapshots, executions, signals, annotations, .kill_switch
├── reports/monthly/             # PDFs + chart PNGs (gitignored)
├── logs/                        # momentum.log rotating (gitignored)
├── docs/STRATEGY.md, ARCHITECTURE.md, OPERATIONS.md
├── notebooks/analysis.ipynb
├── deploy/setup.sh, deploy/trading-scheduler.service
├── Dockerfile, docker-compose.yml, .dockerignore
├── requirements.txt, requirements-dev.txt (black + ruff)
├── .env.example, .gitignore, LICENSE (MIT), WORKFLOW.md, README.md
└── .lavish/us-momentum-plan.html  # build-plan artifact (gitignored)
```

## 3. How to Run

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml

python main.py seed --cash 100000      # first run only; UTC-dated all-cash snapshot
python main.py snapshot                # mark-to-market at yfinance close
python main.py signal [--top-n 3]
python main.py rebalance [--top-n 3] [--threshold 0.02]
python main.py execute                 # dry run: no fills, writes an audit log
python main.py execute --live          # CONFIRM → fills → CSV + snapshot
python main.py stats [START] [END]      # positional dates YYYY-MM-DD (not --start/--end)
python main.py slippage [START] [END] [--threshold 10]
python main.py report 2026-08
python main.py weekly-report [2026-W35]
python main.py annotate [TRADE_ID] [--pre|--post|--list]
python main.py dashboard [--host 127.0.0.1] [--port 5050]
python main.py scheduler [--health-port 8080]
python main.py run-job {snapshot,execute,weekly_report,monthly_report}
python main.py kill-switch {status,activate,deactivate} ["reason"]
python main.py pull                    # DEPRECATED — prints notice, then runs snapshot
```

- `execute` exit codes: **0** success, **1** failure/rejection, **2** fills pending the next open. A wrapper that treats every non-zero as fatal will retry pending runs — that is correct, they need the next session.
- `execute` requires a snapshot younger than 48h (UTC) and aborts if a target symbol has no price.
- `seed` is a no-op when a snapshot exists unless `--force`.
- Every command funnels through `load_config()`, which also calls `setup_logging()`.
- `run-job` accepts `monthly_report` even though `--help` lists three jobs.

## 4. Data Schemas

**Snapshots** `data/snapshots/YYYY-MM-DD.json` — one per UTC date, atomic `.tmp` + `replace`:

```json
{ "timestamp": "2026-08-30T23:23:35.655816Z", "data_source": "yfinance",
  "fill_model": "next_open", "currency": "USD", "asof_close": "2026-08-30",
  "total_equity": 100000.0, "cash": 100000.0,
  "positions": [{ "symbol": "XLK", "quantity": 10, "avg_cost": 100.05,
                  "market_price": 100.0, "market_value": 1000.0, "unrealized_pnl": -0.5 }] }
```

- `data_source` is `"yfinance"` for snapshots; `"yfinance_paper"` appears only in executions.
- `asof_close` is the price date used as the fill reference boundary for `next_open`.
- Zero-quantity positions are dropped. `quantity` is int when integral, else float; prices 4dp, values 2dp.

**Executions** `data/executions/YYYY-MM-DD.csv` — field order fixed in `paper_broker.EXECUTION_FIELDS`:

```
trade_id,timestamp,symbol,asset_class,side,quantity,intended_price,fill_price,
slippage_bps,commission,commission_currency,data_source,fill_model
```

- `asset_class` is always `STK`; `side` is `BUY`/`SELL`; `data_source` is `yfinance_paper`.
- `intended_price` is the reference used (close or next open), `fill_price` includes slippage. `slippage_bps` stores the configured assumption, not a measured difference.
- `fill_model` records which model actually priced the row; legacy rows are backfilled on rewrite. Adding a column means updating `EXECUTION_FIELDS` and `tests/test_paper_broker.py`.
- Appends dedupe by `trade_id`; only real fills are written (dry runs and pending produce no rows).

**Signal logs** `data/signals/YYYY-MM-DD_HHMMSS_<mode>.json` — one file per pipeline run, so dry and live runs coexist. Fields: `timestamp, signal_date, file_date, execution_mode, top_sectors, target_weights, rankings, trades, validation, execution_results, reasoning, data_source, fill_model, slippage_bps`. `fill_model`/`slippage_bps` come from the live config object, not a re-read of the YAML. `retention_days` prunes by filename date.

**Kill switch** `data/.kill_switch`: exists = trading blocked; contents = reason.

## 5. Agent Rules & Conventions

- **Single yfinance gateway.** All network access goes through `src/broker/yfinance_provider`. The only `yf.Ticker` call is the single-symbol fallback inside `get_current_prices` — keep it there.
- **Batch over loop.** One `yf.download(symbols)` per caller, `retry_attempts=2` with backoff; empty/all-NaN is fatal with a user-facing message.
- **Calendar, never hard-coded UTC.** `zoneinfo(config.timezone)` + `XNYS`; the reference repo's fixed `16:30 UTC` cron is the bug this codebase exists to avoid.
- **Paper is source of truth.** No broker connection, no `client_id`, no `connect()` work. Snapshots are mark-to-market at close; fills are deterministic given prices.
- **Never relabel simulated numbers as observed.** Slippage, fills, and PDF metrics are modeled. Label them in output strings, docstrings, and docs.
- **Keep the three cash layers.** Sizer budgets, portfolio clips on apply, broker rejects/clips at submit. Do not bypass the sizer or remove the portfolio clip "for simplicity".
- **Never let a fill silently degrade its model.** If `next_open` cannot price a trade, return `pending`; do not quietly fill at the close. The only exception is a caller that passes no `signal_date` — it logs a warning and falls back.
- **Kill switch first.** Any new order path calls `RiskManager.check_kill_switch()` and lets `KillSwitchActive` propagate so the CLI exits non-zero.
- **Atomic writes.** Any new persistence in `data/` follows the `.tmp` + `replace` pattern.
- **Risk knobs live in `risk_limits`.** The legacy `risk:` block is gone; if an old local config still has it, it is ignored.
- **One scheduler trigger.** Everything fires from the single `tick()` with its `(date, HM, job)` dedupe key. Do not re-add `schedule.every().day.at()` — it used host-local time and double-fired.
- **Currency & display.** `$`; `display_symbol()` strips `.L` for backward compatibility.
- **Broker abstraction.** Future live brokers implement `src.broker.base.Broker` and reuse `ExecutionResult`; `engine.py` and the report formatter depend on that shape.
- **Style.** `from __future__ import annotations`, module-level `logger = logging.getLogger(__name__)`, `# noqa: BLE001` on broad excepts, ASCII-only printed output. `black`/`ruff` are available but unconfigured — don't reformat untouched code.

## 6. Testing

```bash
pytest -q                 # verified: 36 passed
python test_all.py        # same suite via subprocess
python -m py_compile main.py src/*.py src/broker/*.py src/portfolio/*.py src/signals/*.py src/execution/*.py
```

| File | Tests | Covers |
|---|---|---|
| `tests/test_momentum.py` | 6 | 12-1 math, threshold-to-cash, `mode` precedence, custom tickers, short-history drop, all-short-history error |
| `tests/test_position_sizer.py` | 6 | shared cash budget, starved second buy, sells funding buys, commission reservation, `min_trade_shares`, weight threshold |
| `tests/test_rebalance.py` | 2 | preview respects cash, sell proceeds fund buys |
| `tests/test_paper_broker.py` | 7 | dry run, close fill, `next_open` open pricing, pending, cash clipping, unknown model rejection, CSV schema |
| `tests/test_paper_portfolio.py` | 5 | seed/snapshot, buy/sell lots, empty sell, buy clipping, zero-cash skip |
| `tests/test_signal_logger.py` | 4 | per-run filenames, live fill metadata, retention pruning, legacy list files |
| `tests/test_performance.py` | 2 | metrics with snapshots, empty dir |
| `tests/test_scheduler.py` | 4 | trading day, first trading day, cadence monthly/weekly/daily/off |

Still untested: `engine.py` end-to-end (manually verified for pending → filled), `run_scheduler` wall-clock ticks, dashboard routes, PDF export, annotations, SMTP. Add a test or state how you verified when touching those.

## 7. Invariants and Gotchas

1. **`next_open` is genuinely deferred.** Same-day `execute --live` after the close returns `pending` (exit 2) because the next open does not exist yet. Re-running after the session opens fills at that open. Tests assert both halves.
2. **`(1 + returns).prod(min_count=len(returns))` is load-bearing.** After `min_count` was removed, pandas skipped NaNs and a symbol with two months of history produced a plausible 12-1 number that outranked real ones. Never drop `min_count`.
3. **Cash is budgeted once, not per symbol.** `PositionSizer` starts from cash, adds sell proceeds, reserves `commission_per_share`, and clips buys. `PaperPortfolio.apply_fills` clips again as a backstop. Removing either lets cash go negative.
4. **`compute_monthly_returns` drops only all-NaN rows.** `dropna()` (any) would let one short-history ticker truncate the whole frame.
5. **Lookback is month-aligned.** `month_floor(end, months)` guarantees 13 monthly returns; the old `months*31+10` arithmetic landed one sample short near month boundaries.
6. **Universe mode beats tickers.** Both shipped configs used to list all 11 tickers, which silently disabled `classic_9`.
7. **`_XNYS` is captured at import.** If `pandas_market_calendars` is missing, `_is_trading_day` and `_is_first_trading_day_of_month` fall back to weekday logic; tests must not assume holidays.
8. **Scheduler dedupe key is `(date, HH:MM, job)`.** A new job needs its own name; reusing one silently swallows the second trigger.
9. **`execution.mode` gates scheduler fills.** Default `dry_run` keeps the scheduler read-only even when trades are generated.
10. **Risk defaults mirror the YAML now** (`0.35` / `1.00`) and warn when keys are missing. Earlier fallbacks were `0.25`/`0.50`, which rejected valid 33% targets.
11. **Equal weight sits 1.7pp under the cap.** 33.33% vs `0.35`; the engine warns when `1/top_n` exceeds the cap, because then every rebalance fails validation.
12. **`max_turnover_pct: 1.00` is intentional** — initial cash deployment is ~100% turnover. Tighten it only after the first rebalance.
13. **Signal logs are per run, not per day.** Filenames include time and mode; `get_signals_history` sorts by name (chronological) and flattens legacy list files.
14. **Retention deletes by filename date.** A file with an unparseable prefix is skipped, not deleted.
15. **yfinance is unofficial and delayed (~15m).** Rate limits surface as `get_current_prices` returning `{}`; the engine now aborts rather than trading a partial portfolio, and the broker skips symbols without a reference price.
16. **`seed` snapshots claim `data_source: yfinance` with no prices fetched.** Expected — it is an all-cash ledger.
17. **`python main.py pull` still invokes `snapshot`** after the deprecation notice.
18. **Printed output is ASCII.** `--` and `|` replaced the em dash and middot that cp1252 terminals rendered as `?`; keep new output ASCII.
19. **`stats`/`slippage` take positional dates.** `--start` is an argparse error.
20. **`fill_model` is validated at broker construction**, so a typo fails before any order is priced.

## 8. Deployment

- `Dockerfile`: `python:3.11-slim`, `EXPOSE 5050 8080`, `CMD ["python","main.py","scheduler"]`. No IBKR ports.
- `docker-compose.yml`: `scheduler` (`:8080`, mounts `data`, `config`, `reports`, `logs`) and `dashboard` (`:5050`, mounts `data`, `config`).
- `deploy/trading-scheduler.service`: systemd unit, `Restart=always`; edit `User`/`WorkingDirectory`.
- `deploy/setup.sh`: venv + requirements + config copy.
- Health: `GET :8080/` → `{status, et_now, is_trading_day, calendar, rebalance_cadence}`, always `200` (liveness, not readiness).

## 9. References

- Reference repo: `github.com/amr-coding1/live-trading-jan2026` (UK `SXL*.L` + `ib_insync` — replaced here).
- `README.md` (user-facing), `WORKFLOW.md` (daily operator script), `docs/STRATEGY.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`.
- `.lavish/us-momentum-plan.html`: build-plan artifact (Tailwind v4 + DaisyUI v5 + Mermaid); open with `lavish-axi .lavish/us-momentum-plan.html`, poll with `lavish-axi poll`.
