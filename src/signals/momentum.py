"""Momentum signal generator for US sector rotation.

Computes 12-1 momentum (12-month return excluding most recent month)
for US SPDR sector ETFs and ranks them to generate target weights.

US universe defaults to 11 SPDRs (SPY GICS sectors). Supported modes:
- full_11: XLK,XLF,XLE,XLV,XLY,XLP,XLI,XLB,XLU,XLC,XLRE
- classic_9: drops XLC/XLRE for parity with the original UK 9-ETF set.

`universe.mode` wins over `universe.tickers` for the two named baskets; set
`mode: custom` to use a hand-written ticker list. Config-driven; falls back to
full_11 when no config is present.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_MOMENTUM_THRESHOLD = -0.20  # -20%

# Full US SPDR sector universe (GICS 11)
FULL_11 = [
    "XLK",  # Technology
    "XLF",  # Financials
    "XLE",  # Energy
    "XLV",  # Health Care
    "XLY",  # Consumer Discretionary
    "XLP",  # Consumer Staples
    "XLI",  # Industrials
    "XLB",  # Materials
    "XLU",  # Utilities
    "XLC",  # Communication Services
    "XLRE", # Real Estate
]

# Classic 9 for parity with original UK 9
CLASSIC_9 = ["XLK","XLF","XLE","XLV","XLY","XLP","XLI","XLB","XLU"]

# Default — full_11
SECTOR_ETFS = FULL_11

SECTOR_NAMES = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLY": "Cons Discr",
    "XLP": "Cons Staples",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLC": "Communication",
    "XLRE": "Real Estate",
}


def _load_universe_from_config() -> list[str]:
    """Resolve the universe.

    Precedence: an explicit ``universe.mode`` of ``full_11``/``classic_9`` wins,
    because those names describe a fixed basket. ``universe.tickers`` is used
    only when ``mode`` is absent or ``custom``. Previously ``tickers`` always
    won, which made ``mode: classic_9`` a no-op in the shipped config.
    """
    try:
        p = Path("config/config.yaml")
        if not p.exists():
            return SECTOR_ETFS
        cfg = yaml.safe_load(p.read_text()) or {}
        uni = cfg.get("universe", {}) or {}
        mode = str(uni.get("mode", "") or "").strip().lower()
        if mode == "classic_9":
            return CLASSIC_9
        if mode == "full_11":
            return FULL_11
        tickers = uni.get("tickers")
        if tickers:
            return tickers
        return SECTOR_ETFS
    except Exception:  # noqa: BLE001
        return SECTOR_ETFS


def resolve_universe(symbols: Optional[list[str]] = None) -> list[str]:
    if symbols is not None:
        return symbols
    return _load_universe_from_config()


def display_symbol(symbol: str) -> str:
    """US tickers have no suffix; keep compatible with original."""
    return symbol.replace(".L", "")


def download_prices(
    symbols: list[str],
    months: int = 13,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """Backward-compatible wrapper around yfinance_provider.

    Keeps original call sites working; delegates to batch provider.
    """
    from src.broker.yfinance_provider import download_prices as _dl
    return _dl(symbols, months=months, end_date=end_date)


def compute_monthly_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute monthly returns from daily prices (month-end resample).

    Only all-NaN rows are dropped: a symbol with a shorter history keeps its
    NaNs instead of truncating the whole frame for everybody else.
    """
    monthly_prices = prices.resample("ME").last()
    monthly_returns = monthly_prices.pct_change()
    return monthly_returns.dropna(how="all")


def compute_12_1_momentum(monthly_returns: pd.DataFrame) -> pd.Series:
    """Compute 12-1 momentum (12-month return excluding most recent month).

    ``min_count`` is required: pandas' ``prod`` skips NaN by default, which
    would turn a symbol with two months of history into a plausible-looking
    12-1 number instead of a NaN the caller can drop.
    """
    if len(monthly_returns) < 13:
        raise ValueError(f"Need at least 13 months of data, got {len(monthly_returns)}")
    returns_12_months = monthly_returns.iloc[-13:-1]
    cumulative_return = (1 + returns_12_months).prod(min_count=len(returns_12_months)) - 1
    return cumulative_return


def rank_by_momentum(momentum: pd.Series) -> pd.DataFrame:
    """Rank symbols by momentum descending."""
    ranked = pd.DataFrame({
        "symbol": momentum.index,
        "momentum_12_1": momentum.values,
    })
    ranked = ranked.sort_values("momentum_12_1", ascending=False)
    ranked["rank"] = range(1, len(ranked) + 1)
    ranked = ranked.reset_index(drop=True)
    return ranked


def generate_target_weights(
    ranked: pd.DataFrame,
    top_n: int = 3,
    min_momentum: float = MIN_MOMENTUM_THRESHOLD,
) -> pd.DataFrame:
    """Generate equal-weight targets for top N above min_momentum."""
    ranked = ranked.copy()
    eligible = ranked[
        (ranked["rank"] <= top_n) &
        (ranked["momentum_12_1"] >= min_momentum)
    ]
    if len(eligible) == 0:
        logger.warning(f"No sectors meet minimum momentum threshold of {min_momentum:.1%}")
        ranked["target_weight"] = 0.0
        return ranked
    weight = 1.0 / len(eligible)
    ranked["target_weight"] = 0.0
    ranked.loc[eligible.index, "target_weight"] = weight
    if len(eligible) < top_n:
        logger.info(f"Only {len(eligible)} sectors meet threshold, holding {(1 - len(eligible) * weight):.1%} cash")
    return ranked


def generate_momentum_signal(
    symbols: Optional[list[str]] = None,
    top_n: int = 3,
    end_date: Optional[datetime] = None,
) -> dict:
    """Generate complete 12-1 momentum signal.

    Returns dict with signal_date, ranked DataFrame, top_sectors, target_weights, top_n.
    """
    symbols = resolve_universe(symbols)
    if end_date is None:
        end_date = datetime.now(timezone.utc)

    prices = download_prices(symbols, months=13, end_date=end_date)
    monthly_returns = compute_monthly_returns(prices)
    momentum = compute_12_1_momentum(monthly_returns)

    # A symbol with a shorter history (e.g. XLC from 2018) yields NaN instead
    # of failing the whole batch.
    incomplete = [s for s in momentum.index if pd.isna(momentum[s])]
    if incomplete:
        logger.warning(f"Skipping symbols with incomplete 12-1 history: {incomplete}")
        momentum = momentum.dropna()
    if momentum.empty:
        raise ValueError("No symbol has a full 12-month history for the 12-1 signal")

    ranked = rank_by_momentum(momentum)
    ranked = generate_target_weights(ranked, top_n=top_n)

    top_sectors = ranked[ranked["target_weight"] > 0]["symbol"].tolist()
    target_weights = dict(zip(ranked["symbol"], ranked["target_weight"]))

    return {
        "signal_date": end_date.strftime("%Y-%m-%d"),
        "ranked": ranked,
        "top_sectors": top_sectors,
        "target_weights": target_weights,
        "top_n": top_n,
    }


def format_signal_report(signal: dict, cash: float = 0.0) -> str:
    """Format momentum signal as readable text report (USD)."""
    lines = [
        f"MOMENTUM SIGNAL - {signal['signal_date']}",
        "=" * 60,
        "",
        f"{'Rank':<6}{'Symbol':<8}{'Sector':<16}{'12-1 Mom':<12}{'Target'}",
        "-" * 60,
    ]
    for _, row in signal["ranked"].iterrows():
        symbol = row["symbol"]
        display_sym = display_symbol(symbol)
        sector = SECTOR_NAMES.get(symbol, "")
        mom_str = f"{row['momentum_12_1']*100:+.1f}%"
        weight_str = f"{row['target_weight']*100:.1f}%"
        lines.append(f"{row['rank']:<6}{display_sym:<8}{sector:<16}{mom_str:<12}{weight_str}")
    lines.append("")
    lines.append("-" * 60)
    top_display = [display_symbol(s) for s in signal["top_sectors"]]
    lines.append(f"Top {signal['top_n']} sectors: {', '.join(top_display) if top_display else 'NONE (cash)'}")
    if cash > 0:
        lines.append(f"Current cash: ${cash:,.2f}")
    lines.append("")
    lines.append("Data source: Yahoo Finance (yfinance) | Fill model: see config")
    lines.append("")
    return "\n".join(lines)
