# Strategy — US Sector Momentum (12-1)

## Thesis
Cross-sectional momentum on US GICS sectors via SPDR ETFs, monthly rebalance, top-3 equal weight. Academic basis: Jegadeesh & Titman (1993), Moskowitz & Grinblatt (1999), Asness et al. (2013). The 12-1 formulation (12-month cumulative ex-last-month) avoids short-term reversal.

## Universe
11 SPDRs covering S&P 500 GICS sectors (full_11). Optionally `classic_9` drops XLC/XLRE.
Target weight: 33.33% each for top 3; if <3 meet `MIN_MOMENTUM_THRESHOLD = -20%`, hold remainder as cash.

## Signal
`compute_monthly_returns` (month-end resample) → `compute_12_1_momentum` (prod of 12 monthly returns t-13 to t-1) → `rank_by_momentum` → `generate_target_weights`. Batch `yfinance_provider.download_prices` (auto_adjust=True).

## Execution (Paper)
Sizing runs under one shared cash budget: sells first (their proceeds fund the buys), commission reserved, buys clipped rather than overdrawing the ledger.

Reference prices come from yfinance. `data.fill_model` selects the reference:

- `next_open` (default) — the open of the first session after the snapshot's `asof_close`. Same-day runs after the close leave trades `pending`; re-running after the next open fills them there.
- `close` — the latest close, filled immediately.

Fills are simulated at `reference × (1 ± slippage_bps/1e4)` with side-applied sign, and commission is modeled as `commission_per_share`. Portfolio state is a paper ledger (`PaperPortfolio`) with average-cost lots, cash, and mark-to-market snapshots at close.

## Risk
Kill switch file blocks all paper fills. `max_position_pct` (35%) and `max_turnover_pct` (100% allows initial deployment, tighten later). Stale snapshot >48h rejected.

## Differences from UK Reference
- Universe: `SXL*.L` (LSE) → `XL*` (NYSE), GBP→USD, `zoneinfo America/New_York` + XNYS calendar instead of LSE UTC cron.
- Broker: IBKR (`ib_insync`) → yfinance paper (no TWS).
