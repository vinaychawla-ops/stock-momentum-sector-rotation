"""Yahoo Finance data provider — single source of market data.

Centralizes all yfinance access so every caller uses batch downloads,
retry/backoff, and consistent validation. Replaces the scattered
yf.download / yf.Ticker usage in the original repo.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def month_floor(d: date, months_back: int) -> date:
    """First day of the month `months_back` months before `d`.

    Used to build a lookback window that always contains enough
    month-end samples. Day arithmetic (``31 * months``) can land one
    sample short and make the 12-1 signal fail near month boundaries.
    """
    total = d.year * 12 + (d.month - 1) - months_back
    return date(total // 12, total % 12 + 1, 1)


def download_prices(
    symbols: list[str],
    months: int = 13,
    end_date: Optional[datetime] = None,
    auto_adjust: bool = True,
    retry_attempts: int = 2,
) -> pd.DataFrame:
    """Download adjusted close prices for symbols (batch).

    Mirrors the original momentum.download_prices but with retry and
    better error messages. Used for the 12-1 signal (needs 13 months).

    Args:
        symbols: Ticker list (e.g. ["XLK","XLF"]).
        months: Lookback months.
        end_date: End date (UTC). Defaults to now.
        auto_adjust: Pass through to yfinance.
        retry_attempts: Number of retries on empty/failure.

    Returns:
        DataFrame indexed by date with one column per symbol (adjusted close).

    Raises:
        ValueError: If no valid data after retries.
    """
    if end_date is None:
        end_date = datetime.now(timezone.utc)
    # Month-aligned start guarantees >= months+1 month-end samples, so the
    # 12-1 calculation always has 13 monthly returns to work with.
    start = month_floor(end_date.date(), months)
    end = end_date.date() + timedelta(days=1)  # yfinance `end` is exclusive
    logger.info(f"Downloading prices for {len(symbols)} symbols [{start}..{end}]")

    last_err: Optional[Exception] = None
    for attempt in range(retry_attempts + 1):
        try:
            data = yf.download(
                symbols,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=auto_adjust,
            )
            if data is None or data.empty:
                raise ValueError("Empty response from yfinance")

            # Normalize to DataFrame of Closes per symbol
            if isinstance(data.columns, pd.MultiIndex):
                # yfinance returns MultiIndex with level 0 = OHLCV; pick Close
                if "Close" in data.columns.get_level_values(0):
                    prices = data["Close"]
                else:
                    # fallback: take first level if only one symbol shape differs
                    prices = data.xs("Close", axis=1, level=0) if "Close" in str(data.columns) else data
            else:
                # Single symbol case: column may be 'Close' or already normalized
                if "Close" in data.columns:
                    prices = data[["Close"]].copy()
                    prices.columns = symbols if len(symbols) == 1 else prices.columns
                else:
                    prices = data

            # Ensure columns are symbols
            if isinstance(prices, pd.Series):
                prices = prices.to_frame()
                prices.columns = symbols[: len(prices.columns)] if len(prices.columns) else symbols

            # If multi-symbol but yfinance returned single column naming, realign
            if list(prices.columns) != symbols:
                # Try to handle case where yfinance dropped failed tickers silently
                # Keep whatever returned; caller will validate missing.
                pass

            prices = prices.dropna(how="all")
            if prices.empty:
                raise ValueError("No valid price data after cleaning")

            missing = [s for s in symbols if s not in prices.columns or prices[s].isna().all()]
            if missing:
                logger.warning(f"Missing data for symbols: {missing}")
                # If all missing, retry
                if len(missing) == len(symbols):
                    raise ValueError(f"All symbols missing: {missing}")

            logger.info(f"Downloaded {len(prices)} days for {len(prices.columns)} symbols")
            return prices

        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(f"yfinance attempt {attempt+1}/{retry_attempts+1} failed: {e}")
            if attempt < retry_attempts:
                time.sleep(1.5 * (attempt + 1))

    raise ValueError(f"Failed to download prices after {retry_attempts+1} attempts: {last_err}")


def get_current_prices(
    symbols: list[str],
    retry_attempts: int = 2,
) -> dict[str, float]:
    """Batch current price fetch (one yf.download, not a ticker loop).

    Fixes the original rebalance.get_current_prices which looped
    yf.Ticker per symbol (slow + rate-limit prone).

    Returns:
        Dict symbol -> current close (last available).
        Symbols with no data are omitted (caller logs).
    """
    if not symbols:
        return {}
    prices: dict[str, float] = {}
    failed: list[str] = []

    for attempt in range(retry_attempts + 1):
        try:
            # Batch download last 5 trading days to handle weekends/holidays
            data = yf.download(
                symbols,
                period="5d",
                progress=False,
                auto_adjust=True,
            )
            if data is None or data.empty:
                raise ValueError("Empty response")

            if isinstance(data.columns, pd.MultiIndex):
                closes = data["Close"] if "Close" in data.columns.get_level_values(0) else data
            else:
                # single column case
                if "Close" in data.columns:
                    closes = data[["Close"]]
                else:
                    closes = data

            # Normalize: closes is DataFrame date x symbol
            if isinstance(closes, pd.Series):
                closes = closes.to_frame()

            for sym in symbols:
                try:
                    if sym in closes.columns:
                        series = closes[sym].dropna()
                        if not series.empty and series.iloc[-1] > 0:
                            prices[sym] = float(series.iloc[-1])
                            continue
                    # Fallback: try yf.Ticker for single missing symbol (rare)
                    failed.append(sym)
                except Exception:  # noqa: BLE001
                    failed.append(sym)

            # If we got at least some, attempt to fill failures individually once
            if failed and attempt == 0:
                for sym in list(failed):
                    try:
                        t = yf.Ticker(sym)
                        hist = t.history(period="1d")
                        if not hist.empty and float(hist["Close"].iloc[-1]) > 0:
                            prices[sym] = float(hist["Close"].iloc[-1])
                            failed.remove(sym)
                    except Exception:  # noqa: BLE001
                        pass

            if failed:
                logger.warning(f"Failed to get prices for: {failed}")
            return prices

        except Exception as e:  # noqa: BLE001
            logger.warning(f"get_current_prices attempt {attempt+1} failed: {e}")
            if attempt < retry_attempts:
                time.sleep(1.0 * (attempt + 1))
            else:
                logger.error(f"get_current_prices completely failed: {e}")
                return prices

    return prices


def get_ohlc(symbols: list[str], period: str = "5d") -> pd.DataFrame:
    """Fetch recent OHLC for fill modeling (next_open inference).

    Returns raw yfinance DataFrame; caller can extract Open/Close.
    """
    try:
        data = yf.download(symbols, period=period, progress=False, auto_adjust=True)
        return data
    except Exception as e:  # noqa: BLE001
        logger.error(f"get_ohlc failed: {e}")
        return pd.DataFrame()


def get_latest_session_ohlc(symbols: list[str], period: str = "10d") -> dict[str, dict]:
    """Latest session open/close per symbol, for next-open fill pricing.

    Returns ``{symbol: {"date": "YYYY-MM-DD", "open": float, "close": float}}``.
    A symbol whose session is missing from the response is omitted; callers
    decide whether that is fatal.
    """
    if not symbols:
        return {}
    data = get_ohlc(symbols, period=period)
    if data is None or data.empty:
        logger.warning("get_latest_session_ohlc: empty response")
        return {}

    opens: Optional[pd.DataFrame] = None
    closes: Optional[pd.DataFrame] = None
    if isinstance(data.columns, pd.MultiIndex):
        levels = data.columns.get_level_values(0)
        if "Open" not in levels or "Close" not in levels:
            logger.warning(f"get_latest_session_ohlc: unexpected columns {list(levels.unique())}")
            return {}
        opens, closes = data["Open"], data["Close"]
    else:
        if not {"Open", "Close"} <= set(data.columns):
            logger.warning(f"get_latest_session_ohlc: unexpected columns {list(data.columns)}")
            return {}
        single = symbols[0]
        opens = data[["Open"]].rename(columns={"Open": single})
        closes = data[["Close"]].rename(columns={"Close": single})

    out: dict[str, dict] = {}
    for sym in symbols:
        if sym not in opens.columns or sym not in closes.columns:
            continue
        o = opens[sym].dropna()
        c = closes[sym].dropna()
        if o.empty or c.empty:
            continue
        # Use the most recent row where both open and close exist
        joined = pd.concat([o.rename("open"), c.rename("close")], axis=1).dropna()
        if joined.empty:
            continue
        row = joined.iloc[-1]
        out[sym] = {
            "date": str(joined.index[-1].date()),
            "open": float(row["open"]),
            "close": float(row["close"]),
        }
    return out


def health_check(symbols: Optional[list[str]] = None) -> bool:
    """Lightweight health check — can we fetch at least one symbol?"""
    test = symbols or ["XLK"]
    try:
        prices = get_current_prices(test[:1])
        return bool(prices)
    except Exception:  # noqa: BLE001
        return False
