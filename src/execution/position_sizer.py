"""Position sizing — converts target weights to share counts (paper).

Cash is budgeted once, not per symbol: sells are sized first and their
proceeds are added to the budget, then buys consume it in descending
weight-delta order. Commission is reserved per share. A job that would
overdraw the account is clipped instead of driving cash negative.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SizedTrade:
    symbol: str
    action: str  # BUY | SELL
    shares: int
    price: float
    target_weight: float
    current_weight: float
    trade_value: float
    reason: str


class PositionSizer:
    def __init__(self, total_equity: float, cash_available: float, current_positions: dict[str, dict], config: dict):
        self.total_equity = float(total_equity)
        self.cash_available = float(cash_available)
        self.current_positions = current_positions or {}
        self.config = config
        sizing_cfg = config.get("position_sizing", {})
        data_cfg = config.get("data", {})
        self.min_trade_value = float(sizing_cfg.get("min_trade_value", 100))
        self.min_trade_shares = int(sizing_cfg.get("min_trade_shares", 1))
        self.commission_per_share = float(data_cfg.get("commission_per_share", 0.0))

    def _current_weights(self, current_prices: dict[str, float]) -> dict[str, float]:
        weights: dict[str, float] = {}
        if self.total_equity <= 0:
            return weights
        for sym, pos in self.current_positions.items():
            if "market_value" in pos:
                mv = float(pos.get("market_value", 0))
            else:
                mv = float(pos.get("quantity", 0)) * float(
                    current_prices.get(sym, 0) or pos.get("avg_cost", 0)
                )
            weights[sym] = mv / self.total_equity
        return weights

    def generate_trades(
        self,
        target_weights: dict[str, float],
        current_prices: dict[str, float],
        min_threshold: float = 0.02,
    ) -> list[SizedTrade]:
        current_weights = self._current_weights(current_prices)
        all_symbols = set(target_weights) | set(current_weights) | set(self.current_positions)

        sells: list[tuple[float, SizedTrade]] = []
        buys: list[tuple[float, dict]] = []

        for symbol in sorted(all_symbols):
            target = float(target_weights.get(symbol, 0))
            current = float(current_weights.get(symbol, 0))
            diff = target - current
            price = float(current_prices.get(symbol, 0) or 0)

            if abs(diff) < min_threshold:
                continue
            if price <= 0:
                logger.warning(f"Skipping {symbol}: no price for sizing")
                continue

            qty_held = float(self.current_positions.get(symbol, {}).get("quantity", 0))

            if diff > 0:
                buys.append((diff, {
                    "symbol": symbol, "target": target, "current": current,
                    "price": price, "target_value": target * self.total_equity,
                    "current_value": current * self.total_equity,
                }))
                continue

            value_diff = (current - target) * self.total_equity
            shares = math.floor(value_diff / price) if price else 0
            shares = min(shares, int(qty_held))
            if shares < max(self.min_trade_shares, 1):
                continue
            trade_value = shares * price
            if trade_value < self.min_trade_value:
                continue
            sells.append((abs(diff), SizedTrade(
                symbol=symbol, action="SELL", shares=shares, price=price,
                target_weight=target, current_weight=current,
                trade_value=trade_value,
                reason=f"Weight {current*100:.1f}% -> {target*100:.1f}%",
            )))

        # Sells fund the buys
        budget = self.cash_available
        sized: list[SizedTrade] = []
        for _, trade in sorted(sells, key=lambda x: -x[0]):
            budget += trade.trade_value - trade.shares * self.commission_per_share
            sized.append(trade)

        # Buys consume the shared budget, largest gap first
        for _, b in sorted(buys, key=lambda x: -x[0]):
            unit_cost = b["price"] + self.commission_per_share
            desired_value = b["target_value"] - b["current_value"]
            affordable_shares = int(max(budget, 0.0) / unit_cost) if unit_cost > 0 else 0
            shares = min(math.floor(desired_value / b["price"]), affordable_shares)
            if shares < max(self.min_trade_shares, 1):
                if affordable_shares >= max(self.min_trade_shares, 1):
                    logger.info(f"Skipping BUY {b['symbol']}: min_trade_shares={self.min_trade_shares}")
                else:
                    logger.info(
                        f"Skipping BUY {b['symbol']}: cash budget ${budget:,.2f} "
                        f"covers {affordable_shares} shares"
                    )
                continue
            trade_value = shares * b["price"]
            if trade_value < self.min_trade_value:
                continue
            budget -= shares * unit_cost
            sized.append(SizedTrade(
                symbol=b["symbol"], action="BUY", shares=shares, price=b["price"],
                target_weight=b["target"], current_weight=b["current"],
                trade_value=trade_value,
                reason=f"Weight {b['current']*100:.1f}% -> {b['target']*100:.1f}%",
            ))

        # Sells first (free cash), then largest gap
        sized.sort(key=lambda t: (0 if t.action == "SELL" else 1, -abs(t.target_weight - t.current_weight)))
        return sized
