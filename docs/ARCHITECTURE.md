# Architecture — US Paper (Yahoo Finance)

```
yfinance Provider (batch) ──► momentum (12-1) ──► rebalance (current vs target, cash-budgeted)
                │
                ├──► get_latest_session_ohlc (open/close per session)
                │
                └─► PaperPortfolio (cash + average-cost lots) ──► snapshots JSON (mark-to-market)
                          │
                          └─► PaperBroker (fills: next_open | close, + slippage) ──► executions CSV
                                     │                 ▲
                                     │                 └── pending trades when the next open
                                     │                     does not exist yet (exit code 2)
                                     └─► Engine (signal → snapshot → prices → sizer → risk → broker → audit)
                                              │
                                              └─► SignalLogger (data/signals/YYYY-MM-DD_HHMMSS_mode.json)

snapshots + executions ──► performance (Sharpe/DD/win) ──► dashboard (:5050) + export (ReportLab PDF)
                                     │
                                     └─► slippage_analyzer (modeled bps)

scheduler ──► single 30s tick on the exchange clock, deduped per (date, time, job)
              snapshot 16:05 on trading days | execute 16:10 per rebalance_cadence
              weekly report Sunday 21:00
health :8080 ──► {et_now, is_trading_day, calendar, rebalance_cadence}

logging_setup ──► console + rotating logs/momentum.log
```

## Key Modules
- `src/broker/yfinance_provider.py` — single yfinance gateway (batch download, month-aligned lookback, retry, session OHLC, health_check)
- `src/broker/paper_broker.py` — fill models, cash guard, atomic executions CSV, post-fill snapshot
- `src/broker/base.py` — `ExecutionResult` dataclass, `Broker` protocol (future LiveBroker can implement)
- `src/portfolio/paper_portfolio.py` — cash ledger, average-cost lots, atomic JSON snapshot, buy clipping
- `src/signals/momentum.py` — 12-1 with `min_count` NaN safety, universe modes (`full_11`/`classic_9`/`custom`)
- `src/signals/rebalance.py` — batch prices, cash-budgeted preview
- `src/execution/position_sizer.py` — one shared budget, commission reserved
- `src/execution/engine.py` — paper pipeline, 48h staleness guard, pending propagation
- `src/scheduler.py` — exchange-time tick, cadence, dedupe, XNYS calendar
- `src/logging_setup.py` — root logger configuration

## Fill model
`fill_model` is validated at broker construction.

| Model | Reference price | Timing |
|---|---|---|
| `next_open` | Open of the first session after the snapshot's `asof_close` | Deferred; `pending` until that session opens |
| `close` | Latest available close | Same run |

`intended_price` in the CSV is the reference used; `fill_price` adds `slippage_bps`. The CSV also records which model priced the row.

## Schemas
- `executions/*.csv`: `…, data_source=yfinance_paper, fill_model`
- `snapshots/*.json`: `data_source, fill_model, asof_close, currency`
- `signals/*.json`: one file per run, includes `fill_model` and `slippage_bps`

## Data Flow
Snapshot is the source of truth (not a broker). Mark-to-market uses the yfinance close. Slippage is modeled, not observed — labeled everywhere. Because one missing price would otherwise produce a partial portfolio, the engine aborts the run when a target symbol has no price.
