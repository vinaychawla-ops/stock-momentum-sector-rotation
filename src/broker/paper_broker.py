"""Paper broker — deterministic fill simulation using yfinance.

No ib_insync, no sockets. Fills are synthetic:

    fill_price = reference_price * (1 +/- slippage_bps/1e4)

Reference price depends on ``fill_model``:

* ``close``      — the latest available close (same-session fill).
* ``next_open``  — the open of the first session *after* the snapshot the
  trade list was built from (``signal_date``). If that session has not
  opened yet the trade is reported ``pending`` and no CSV row is written;
  re-running after the next open fills it at that open.

Writes an executions CSV and updates the PaperPortfolio ledger atomically.
Buys are clipped to available cash so the ledger cannot go negative.
"""
from __future__ import annotations

import csv
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .base import ExecutionResult

logger = logging.getLogger(__name__)

VALID_FILL_MODELS = ("next_open", "close")

EXECUTION_FIELDS = [
    "trade_id", "timestamp", "symbol", "asset_class", "side", "quantity",
    "intended_price", "fill_price", "slippage_bps", "commission",
    "commission_currency", "data_source", "fill_model",
]


def _calc_fill_price(reference: float, side: str, slippage_bps: float) -> float:
    if reference <= 0:
        return reference
    bps = slippage_bps / 10000.0
    if side == "BUY":
        return round(reference * (1 + bps), 4)
    return round(reference * (1 - bps), 4)


class PaperBroker:
    """Simulates order fills and persists executions."""

    def __init__(self, config: dict, dry_run: bool = True, portfolio=None):
        self.config = config
        self.dry_run = dry_run
        self.portfolio = portfolio
        data_cfg = config.get("data", {})
        exec_cfg = config.get("execution", {})
        self.fill_model: str = exec_cfg.get("fill_model") or data_cfg.get("fill_model", "next_open")
        if self.fill_model not in VALID_FILL_MODELS:
            raise ValueError(
                f"Unknown fill_model {self.fill_model!r}; expected one of {VALID_FILL_MODELS}"
            )
        self.slippage_bps: float = float(data_cfg.get("slippage_bps", 5))
        self.commission_per_share: float = float(data_cfg.get("commission_per_share", 0.0))
        self.executions_dir = Path(config["paths"]["executions"])
        self.executions_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> bool:
        # No connection needed for paper; keep interface compatible
        return True

    def disconnect(self) -> None:
        pass

    def _resolve_references(
        self, symbols: list[str], signal_date: Optional[str]
    ) -> tuple[dict[str, float], list[str]]:
        """Return (reference prices, symbols whose next open is not available yet)."""
        from src.broker.yfinance_provider import get_current_prices, get_latest_session_ohlc

        if self.dry_run or self.fill_model == "close":
            # Dry run is a preview built from the latest close; live `close`
            # fills at that same close.
            return get_current_prices(symbols), []

        if not signal_date:
            logger.warning(
                "next_open fill model without signal_date — falling back to the latest close. "
                "Pass the snapshot's asof_close to get true next-open fills."
            )
            return get_current_prices(symbols), []

        sessions = get_latest_session_ohlc(symbols)
        refs: dict[str, float] = {}
        pending: list[str] = []
        unresolved: list[str] = []
        for sym in symbols:
            session = sessions.get(sym)
            if session is None:
                unresolved.append(sym)
                continue
            if session["date"] > signal_date:
                refs[sym] = session["open"]
            else:
                pending.append(sym)

        if unresolved:
            # Session data missing for a symbol: fall back to its close so the
            # trade still fills, but say so.
            logger.warning(f"No session OHLC for {unresolved}; using latest close")
            refs.update(get_current_prices(unresolved))
        return refs, pending

    def submit_batch(self, trades: list[dict], signal_date: Optional[str] = None) -> list[ExecutionResult]:
        """Simulate batch fills.

        Args:
            trades: items from PositionSizer —
                ``{symbol, action BUY|SELL, shares, price (reference), reason}``.
            signal_date: snapshot ``asof_close`` (YYYY-MM-DD) the trade list was
                derived from. Required for true ``next_open`` pricing.

        Returns:
            One ExecutionResult per input trade, in input order. Statuses are
            ``filled``, ``dry_run``, ``pending`` or ``rejected``.
        """
        if not trades:
            return []

        symbols = [t["symbol"] for t in trades]
        ref_prices, pending_symbols = self._resolve_references(symbols, signal_date)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # --- Pass 1: price every trade and decide fill / pending / reject ----
        entries: list[dict] = []
        for t in trades:
            symbol = t["symbol"]
            action = t["action"]
            shares = int(t.get("shares", 0) or t.get("shares_to_trade", 0) or 0)
            if shares <= 0:
                continue

            if symbol in pending_symbols:
                entries.append({
                    "trade": t, "symbol": symbol, "action": action, "shares": shares,
                    "status": "pending", "reference": None, "fill_price": None,
                    "message": "Next open not available yet; re-run after the next session opens",
                })
                continue

            ref = float(ref_prices.get(symbol, t.get("price", 0)) or t.get("price", 0) or 0)
            if ref <= 0:
                logger.warning(f"Skipping {symbol}: no reference price")
                entries.append({
                    "trade": t, "symbol": symbol, "action": action, "shares": shares,
                    "status": "rejected", "reference": None, "fill_price": None,
                    "message": "No reference price",
                })
                continue

            entries.append({
                "trade": t, "symbol": symbol, "action": action, "shares": shares,
                "status": "dry_run" if self.dry_run else "filled",
                "reference": ref,
                "fill_price": ref if self.dry_run else _calc_fill_price(ref, action, self.slippage_bps),
                "message": "",
            })

        # --- Pass 2: apply the cash budget (sells free cash first) ----------
        if not self.dry_run and self.portfolio is not None:
            budget = float(self.portfolio.cash)
            for e in entries:
                if e["status"] == "filled" and e["action"] == "SELL":
                    budget += e["shares"] * e["fill_price"] - e["shares"] * self.commission_per_share
            for e in entries:
                if e["status"] != "filled" or e["action"] != "BUY":
                    continue
                unit_cost = e["fill_price"] + self.commission_per_share
                cost = e["shares"] * unit_cost
                if cost <= budget + 1e-9:
                    budget -= cost
                    continue
                affordable = int(budget / unit_cost) if unit_cost > 0 else 0
                if affordable <= 0:
                    e["status"] = "rejected"
                    e["message"] = f"Insufficient cash: need ${cost:,.2f}, have ${budget:,.2f}"
                    logger.warning(f"Rejecting BUY {e['shares']} {e['symbol']}: {e['message']}")
                    continue
                logger.warning(
                    f"Clipping BUY {e['symbol']} from {e['shares']} to {affordable} shares "
                    f"(cash ${budget:,.2f})"
                )
                e["message"] = f"Clipped from {e['shares']} to {affordable} shares to fit cash"
                e["shares"] = affordable
                budget -= affordable * unit_cost

        # --- Pass 3: build results, persist fills ---------------------------
        results: list[ExecutionResult] = []
        fills_to_apply: list[dict] = []
        csv_rows: list[dict] = []

        for e in entries:
            symbol, action, shares = e["symbol"], e["action"], e["shares"]
            status, fill_price, ref = e["status"], e["fill_price"], e["reference"]

            if status == "pending":
                results.append(ExecutionResult(
                    order_id=f"PEND-{uuid.uuid4().hex[:6]}",
                    symbol=symbol, action=action, shares=shares,
                    order_type=self.fill_model, status="pending",
                    message=e["message"], dry_run=False,
                ))
                continue

            if status == "rejected":
                results.append(ExecutionResult(
                    order_id=f"REJ-{uuid.uuid4().hex[:6]}",
                    symbol=symbol, action=action, shares=shares,
                    order_type=self.fill_model, status="rejected",
                    message=e["message"], dry_run=self.dry_run,
                ))
                continue

            if status == "dry_run":
                results.append(ExecutionResult(
                    order_id=f"DRY-{uuid.uuid4().hex[:6]}",
                    symbol=symbol, action=action, shares=shares,
                    order_type=self.fill_model, status="dry_run",
                    fill_price=ref, fill_time=now_iso,
                    message=(
                        f"Dry run: would fill {shares}@{ref:.2f} "
                        f"({self.fill_model}+{self.slippage_bps}bps)"
                    ),
                    dry_run=True,
                ))
                continue

            assert fill_price is not None and ref is not None
            commission = round(shares * self.commission_per_share, 2)
            trade_id = str(uuid.uuid4())
            note = f"Paper filled {shares}@{fill_price:.2f} (ref {ref:.2f} {self.fill_model} +{self.slippage_bps}bps)"
            if e["message"]:
                note = f"{note} — {e['message']}"

            fills_to_apply.append({
                "symbol": symbol, "side": action, "quantity": shares,
                "fill_price": fill_price, "commission": commission,
            })
            csv_rows.append({
                "trade_id": trade_id,
                "timestamp": now_iso,
                "symbol": symbol,
                "asset_class": "STK",
                "side": action,
                "quantity": shares,
                "intended_price": ref,
                "fill_price": fill_price,
                "slippage_bps": self.slippage_bps,
                "commission": commission,
                "commission_currency": "USD",
                "data_source": "yfinance_paper",
                "fill_model": self.fill_model,
            })
            results.append(ExecutionResult(
                order_id=trade_id[:8],
                symbol=symbol, action=action, shares=shares,
                order_type=self.fill_model, status="filled",
                fill_price=fill_price, fill_time=now_iso,
                message=note, dry_run=False,
            ))

        # Persist only real fills
        if not self.dry_run and csv_rows:
            self._append_executions(csv_rows)
            if self.portfolio is not None:
                self.portfolio.apply_fills(fills_to_apply, commission_per_share=self.commission_per_share)
                self._save_post_fill_snapshot(csv_rows)

        return results

    def _save_post_fill_snapshot(self, csv_rows: list[dict]) -> None:
        if self.portfolio is None:
            return
        try:
            from src.broker.yfinance_provider import get_current_prices

            held = list(self.portfolio.positions.keys())
            snap_prices: dict[str, float] = {}
            if held:
                snap_prices = get_current_prices(held)
            for row in csv_rows:
                sym = row["symbol"]
                if sym not in snap_prices or snap_prices[sym] <= 0:
                    snap_prices[sym] = float(row["fill_price"])
            for sym, pos in self.portfolio.positions.items():
                if sym not in snap_prices or snap_prices[sym] <= 0:
                    snap_prices[sym] = float(pos.get("avg_cost", 0))
            self.portfolio.save_snapshot(snap_prices, fill_model=self.fill_model)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to save post-fill snapshot: {e}")

    def _append_executions(self, rows: list[dict]) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_path = self.executions_dir / f"{date_str}.csv"
        tmp = self.executions_dir / f"{date_str}.csv.tmp"

        existing_rows: list[dict] = []
        if file_path.exists():
            try:
                with open(file_path, newline="") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        existing_rows = list(reader)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to read existing executions, overwriting: {e}")
                existing_rows = []

        seen = {r.get("trade_id") for r in existing_rows}
        for r in rows:
            if r["trade_id"] not in seen:
                existing_rows.append(r)
                seen.add(r["trade_id"])

        with open(tmp, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=EXECUTION_FIELDS)
            writer.writeheader()
            for r in existing_rows:
                out = {k: r.get(k, "") for k in EXECUTION_FIELDS}
                if not out.get("data_source"):
                    out["data_source"] = "yfinance_paper"
                if not out.get("fill_model"):
                    out["fill_model"] = self.fill_model
                writer.writerow(out)
        tmp.replace(file_path)
        logger.info(f"Appended {len(rows)} executions to {file_path}")
        return file_path
