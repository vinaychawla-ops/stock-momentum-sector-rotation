"""Paper portfolio — deterministic cash + position ledger fed by yfinance closes.

Replaces IBKR portfolio snapshot. Snapshot JSON shape is kept compatible
with the original (total_equity, cash, positions[]) but adds
data_source/fill_model/asof_close for auditability.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PaperPortfolio:
    """In-memory paper portfolio with atomic snapshot persistence."""

    def __init__(
        self,
        cash: float = 100000.0,
        positions: Optional[dict[str, dict]] = None,
        snapshots_dir: str = "data/snapshots",
        currency: str = "USD",
    ):
        self.cash = float(cash)
        # positions: symbol -> {quantity, avg_cost}
        self.positions: dict[str, dict] = positions or {}
        self.snapshots_dir = snapshots_dir
        self.currency = currency

    @classmethod
    def load_latest(cls, snapshots_dir: str) -> Optional["PaperPortfolio"]:
        path = Path(snapshots_dir)
        if not path.exists():
            return None
        files = sorted(path.glob("*.json"), reverse=True)
        if not files:
            return None
        with open(files[0]) as f:
            data = json.load(f)
        positions = {}
        for p in data.get("positions", []):
            sym = p.get("symbol")
            if sym:
                positions[sym] = {"quantity": float(p.get("quantity", 0)), "avg_cost": float(p.get("avg_cost", 0))}
        return cls(
            cash=float(data.get("cash", 0)),
            positions=positions,
            snapshots_dir=snapshots_dir,
            currency=data.get("currency", "USD"),
        )

    @classmethod
    def seed(cls, snapshots_dir: str, initial_cash: float = 100000.0, currency: str = "USD") -> "PaperPortfolio":
        """Create fresh portfolio with only cash (no positions)."""
        Path(snapshots_dir).mkdir(parents=True, exist_ok=True)
        return cls(cash=initial_cash, positions={}, snapshots_dir=snapshots_dir, currency=currency)

    def market_value(self, prices: dict[str, float]) -> float:
        mv = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym, pos.get("avg_cost", 0))
            mv += float(pos.get("quantity", 0)) * float(px or 0)
        return mv

    def total_equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def to_snapshot(self, prices: dict[str, float], timestamp: Optional[str] = None, fill_model: str = "next_open", data_source: str = "yfinance") -> dict:
        ts = timestamp or _now_iso()
        positions_list = []
        for sym, pos in sorted(self.positions.items()):
            qty = float(pos.get("quantity", 0))
            if qty == 0:
                continue
            avg_cost = float(pos.get("avg_cost", 0))
            mkt_price = float(prices.get(sym, avg_cost) or avg_cost)
            market_value = qty * mkt_price
            unrealized = (mkt_price - avg_cost) * qty
            positions_list.append({
                "symbol": sym,
                "quantity": int(qty) if float(qty).is_integer() else qty,
                "avg_cost": round(avg_cost, 4),
                "market_price": round(mkt_price, 4),
                "market_value": round(market_value, 2),
                "unrealized_pnl": round(unrealized, 2),
            })
        total_eq = round(self.total_equity(prices), 2)
        return {
            "timestamp": ts,
            "data_source": data_source,
            "fill_model": fill_model,
            "currency": self.currency,
            "asof_close": ts[:10],
            "total_equity": total_eq,
            "cash": round(float(self.cash), 2),
            "positions": positions_list,
        }

    def save_snapshot(self, prices: dict[str, float], fill_model: str = "next_open") -> Path:
        """Atomically write snapshot JSON for today (UTC date)."""
        snap = self.to_snapshot(prices, fill_model=fill_model)
        out_dir = Path(self.snapshots_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_path = out_dir / f"{date_str}.json"
        tmp = out_dir / f"{date_str}.json.tmp"
        tmp.write_text(json.dumps(snap, indent=2))
        tmp.replace(file_path)
        logger.info(f"Saved paper snapshot to {file_path} (equity ${snap['total_equity']:,.2f})")
        return file_path

    def save_snapshot_with_prices(self, fill_model: str = "next_open") -> Path:
        """Fetch current prices for held positions + cash, then snapshot."""
        symbols = list(self.positions.keys())
        prices: dict[str, float] = {}
        if symbols:
            from src.broker.yfinance_provider import get_current_prices
            prices = get_current_prices(symbols)
        # Fallback: use avg_cost where price missing
        for sym, pos in self.positions.items():
            if sym not in prices or prices[sym] <= 0:
                prices[sym] = float(pos.get("avg_cost", 0))
        return self.save_snapshot(prices, fill_model=fill_model)

    def apply_fills(self, fills: list[dict], commission_per_share: float = 0.0) -> None:
        """Apply synthetic fills (BUY/SELL) to ledger.

        Each fill: {symbol, side BUY|SELL, quantity, fill_price, commission?}
        Updates cash and creates/adjusts positions with average-cost tracking.
        Sells reduce quantity; if quantity hits 0, position is removed.
        Buys that would overdraw cash are clipped to what is affordable, so the
        ledger can never carry a negative cash balance.
        """
        for fill in fills:
            symbol = fill["symbol"]
            side = fill["side"]
            qty = int(fill["quantity"])
            price = float(fill["fill_price"])
            commission = float(fill.get("commission", qty * commission_per_share))

            if side == "BUY":
                unit_cost = price + commission_per_share
                affordable = int(self.cash / unit_cost) if unit_cost > 0 else 0
                if qty > affordable:
                    logger.warning(
                        f"BUY {qty} {symbol} @ {price:.2f} needs ${qty * unit_cost:,.2f}, "
                        f"cash is ${self.cash:,.2f} — clipping to {affordable} shares"
                    )
                    qty = affordable
                    commission = round(qty * commission_per_share, 2)
                    if qty <= 0:
                        continue
                cost = qty * price + commission
                self.cash -= cost
                existing = self.positions.get(symbol)
                if existing is None or existing["quantity"] == 0:
                    self.positions[symbol] = {"quantity": float(qty), "avg_cost": price}
                else:
                    old_qty = float(existing["quantity"])
                    old_cost = float(existing["avg_cost"])
                    new_qty = old_qty + qty
                    new_avg = (old_qty * old_cost + qty * price) / new_qty if new_qty else price
                    self.positions[symbol] = {"quantity": new_qty, "avg_cost": new_avg}
            elif side == "SELL":
                existing = self.positions.get(symbol)
                held = int(float(existing["quantity"])) if existing else 0
                if qty > held:
                    logger.warning(
                        f"Selling {qty} {symbol} but only {held} held — clipping to {held}"
                    )
                    qty = held
                    if qty <= 0:
                        continue
                    commission = round(qty * commission_per_share, 2)
                proceeds = qty * price - commission
                self.cash += proceeds
                existing["quantity"] = float(existing["quantity"]) - qty
                if existing["quantity"] <= 1e-9:
                    del self.positions[symbol]
            else:
                logger.warning(f"Unknown side {side} for {symbol}")

    def to_dict(self) -> dict:
        return {"cash": self.cash, "positions": self.positions, "snapshots_dir": self.snapshots_dir}
