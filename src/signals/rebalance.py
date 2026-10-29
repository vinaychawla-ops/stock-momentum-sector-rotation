"""Portfolio rebalancing logic — US / Yahoo Finance edition.

Compares current paper-portfolio snapshot to target weights from
momentum signal and generates a trade list. Uses batch yfinance_provider
instead of per-ticker yf.Ticker loop (fixes original rate-limit issue).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_MIN_TRADE_THRESHOLD = 0.02


def load_latest_snapshot(snapshots_dir: str) -> Optional[dict]:
    """Load most recent portfolio snapshot (paper JSON)."""
    snap_path = Path(snapshots_dir)
    if not snap_path.exists():
        logger.warning(f"Snapshots directory not found: {snapshots_dir}")
        return None
    json_files = sorted(snap_path.glob("*.json"), reverse=True)
    if not json_files:
        logger.warning("No snapshot files found")
        return None
    latest = json_files[0]
    logger.info(f"Loading snapshot from {latest.name}")
    with open(latest) as f:
        return json.load(f)


def get_current_weights(snapshot: dict) -> dict[str, float]:
    """Calculate current weights from snapshot."""
    total_equity = snapshot.get("total_equity", 0)
    if total_equity <= 0:
        return {}
    weights: dict[str, float] = {}
    for pos in snapshot.get("positions", []):
        symbol = pos.get("symbol")
        market_value = pos.get("market_value", 0)
        if symbol and market_value:
            weights[symbol] = market_value / total_equity
    return weights


def get_current_prices(symbols: list[str]) -> dict[str, float]:
    """Batch price fetch via yfinance_provider (no per-ticker loop)."""
    from src.broker.yfinance_provider import get_current_prices as _batch
    return _batch(symbols)


def calculate_trades(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    total_equity: float,
    current_prices: dict[str, float],
    current_positions: dict[str, float],
    min_threshold: float = DEFAULT_MIN_TRADE_THRESHOLD,
) -> pd.DataFrame:
    """Calculate required trades to rebalance."""
    all_symbols = set(current_weights.keys()) | set(target_weights.keys())
    trades: list[dict] = []
    for symbol in sorted(all_symbols):
        current = current_weights.get(symbol, 0)
        target = target_weights.get(symbol, 0)
        diff = target - current
        price = current_prices.get(symbol, 0)

        if abs(diff) < min_threshold:
            action = "HOLD"
            shares_to_trade = 0
        elif price <= 0:
            logger.warning(f"Skipping {symbol}: no valid price")
            action = "HOLD"
            shares_to_trade = 0
        elif diff > 0:
            action = "BUY"
            target_value = target * total_equity
            current_value = current * total_equity
            value_diff = target_value - current_value
            shares_to_trade = math.floor(value_diff / price)
            remainder = value_diff - (shares_to_trade * price)
            if remainder > 0:
                logger.debug(f"{symbol}: {remainder:.2f} cash remainder")
        else:
            action = "SELL"
            current_shares = current_positions.get(symbol, 0)
            target_value = target * total_equity
            current_value = current * total_equity
            value_diff = current_value - target_value
            shares_to_sell = math.floor(value_diff / price)
            shares_to_trade = min(shares_to_sell, int(current_shares))

        trades.append({
            "symbol": symbol,
            "current_weight": current,
            "target_weight": target,
            "weight_diff": diff,
            "action": action,
            "shares_to_trade": abs(int(shares_to_trade)),
            "price": current_prices.get(symbol, 0),
        })

    df = pd.DataFrame(trades)
    if df.empty:
        return df
    df = df[df["action"] != "HOLD"]
    if df.empty:
        return df

    # Same cash discipline as PositionSizer: sells fund buys, buys share one
    # budget. Without this the preview could show orders the ledger cannot pay
    # for, and `execute` would then clip them.
    budget = total_equity * max(0.0, 1.0 - sum(current_weights.values()))
    for idx in df.index[df["action"] == "SELL"]:
        budget += float(df.at[idx, "shares_to_trade"]) * float(df.at[idx, "price"])
    buy_order = df[df["action"] == "BUY"].sort_values("weight_diff", ascending=False).index
    for idx in buy_order:
        price = float(df.at[idx, "price"])
        shares = int(df.at[idx, "shares_to_trade"])
        if price <= 0:
            continue
        affordable = int(budget / price)
        if shares > affordable:
            logger.info(
                f"{df.at[idx, 'symbol']}: clipping BUY from {shares} to {affordable} shares "
                f"(available ${budget:,.2f})"
            )
            shares = affordable
            df.at[idx, "shares_to_trade"] = shares
        budget -= shares * price
    df = df[df["shares_to_trade"] > 0]

    if df.empty:
        return df
    df = df.sort_values(["action", "weight_diff"], ascending=[True, False]).reset_index(drop=True)
    return df


def generate_rebalance_trades(
    snapshots_dir: str,
    top_n: int = 3,
    min_threshold: float = DEFAULT_MIN_TRADE_THRESHOLD,
) -> dict:
    """Generate rebalance trade list from latest paper snapshot + momentum signal."""
    from .momentum import generate_momentum_signal

    snapshot = load_latest_snapshot(snapshots_dir)
    if snapshot is None:
        raise ValueError("No portfolio snapshot found. Run 'python main.py seed' or 'python main.py snapshot' first.")

    signal = generate_momentum_signal(top_n=top_n)
    current_weights = get_current_weights(snapshot)
    target_weights = signal["target_weights"]

    all_symbols = list(set(current_weights.keys()) | set(target_weights.keys()))
    current_prices = get_current_prices(all_symbols)

    current_positions: dict[str, float] = {}
    for pos in snapshot.get("positions", []):
        sym = pos.get("symbol")
        qty = pos.get("quantity", 0)
        if sym:
            current_positions[sym] = float(qty)

    trades = calculate_trades(
        current_weights=current_weights,
        target_weights=target_weights,
        total_equity=snapshot.get("total_equity", 0),
        current_prices=current_prices,
        current_positions=current_positions,
        min_threshold=min_threshold,
    )

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "signal": signal,
        "snapshot": snapshot,
        "trades": trades,
        "current_weights": current_weights,
        "target_weights": target_weights,
        "total_equity": snapshot.get("total_equity", 0),
        "cash": snapshot.get("cash", 0),
    }


def format_rebalance_report(rebalance: dict) -> str:
    """Format rebalance trades as readable text (USD)."""
    lines = [
        f"REBALANCE TRADES - {rebalance['date']}",
        "=" * 60,
        "",
        f"Total Equity: ${rebalance['total_equity']:,.2f}",
        f"Cash Available: ${rebalance['cash']:,.2f}",
        "",
    ]
    trades: pd.DataFrame = rebalance["trades"]
    if trades.empty:
        lines.append("No trades required. Portfolio is within threshold.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"{'Symbol':<8}{'Current':<10}{'Target':<10}{'Action':<8}{'Shares':<8}{'Est. Value'}")
    lines.append("-" * 60)
    for _, row in trades.iterrows():
        current_str = f"{row['current_weight']*100:.1f}%"
        target_str = f"{row['target_weight']*100:.1f}%"
        est_value = row["shares_to_trade"] * row["price"]
        lines.append(
            f"{row['symbol']:<8}{current_str:<10}{target_str:<10}"
            f"{row['action']:<8}{row['shares_to_trade']:<8}${est_value:,.0f}"
        )
    lines.append("")
    lines.append("-" * 60)
    buy_count = len(trades[trades["action"] == "BUY"])
    sell_count = len(trades[trades["action"] == "SELL"])
    lines.append(f"Total trades: {len(trades)} ({buy_count} buys, {sell_count} sells)")
    # Use vectorized sum safely
    try:
        buy_value = float((trades[trades["action"] == "BUY"].apply(lambda r: r["shares_to_trade"] * r["price"], axis=1)).sum()) if buy_count else 0
        sell_value = float((trades[trades["action"] == "SELL"].apply(lambda r: r["shares_to_trade"] * r["price"], axis=1)).sum()) if sell_count else 0
    except Exception:
        buy_value = sell_value = 0
    lines.append(f"Estimated buy value: ${buy_value:,.2f}")
    lines.append(f"Estimated sell value: ${sell_value:,.2f}")
    lines.append(f"Net cash flow: ${sell_value - buy_value:,.2f}")
    lines.append("")
    lines.append("Paper fills: next trading day open + slippage (see config). Then run:")
    lines.append("  python main.py snapshot   # mark-to-market at close")
    lines.append("")
    return "\n".join(lines)
