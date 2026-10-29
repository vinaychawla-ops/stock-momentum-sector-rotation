"""Execution engine — paper variant (no IBKR).

7-step pipeline:
 1. Generate momentum signal
 2. Load paper snapshot
 3. Fetch prices (batch yfinance)
 4. Calculate sized trades
 5. Validate (risk)
 6. Execute via PaperBroker (dry_run vs paper-live)
 7. Audit log
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from src.signals.momentum import generate_momentum_signal
from src.signals.rebalance import load_latest_snapshot, get_current_weights
from src.broker.paper_broker import PaperBroker
from src.execution.position_sizer import PositionSizer
from src.execution.risk_manager import RiskManager, KillSwitchActive
from src.execution.signal_logger import SignalLogger, format_signal_reasoning
from src.portfolio.paper_portfolio import PaperPortfolio

logger = logging.getLogger(__name__)


@dataclass
class ExecutionReport:
    timestamp: str
    execution_mode: str
    signal_date: str
    rankings: list[dict]
    top_sectors: list[str]
    target_weights: dict[str, float]
    current_weights: dict[str, float]
    trades: list[dict]
    validation_result: Optional[dict]
    execution_results: list[dict]
    pending: list[str]
    total_equity: float
    cash: float
    reasoning: str
    success: bool
    error_message: str = ""


class ExecutionEngine:
    def __init__(self, config: dict, dry_run: bool = True):
        self.config = config
        self.dry_run = dry_run
        self.risk_manager = RiskManager(config)
        signals_cfg = config.get("signals", {}) or {}
        data_cfg = config.get("data", {}) or {}
        exec_cfg = config.get("execution", {}) or {}
        self.fill_model = exec_cfg.get("fill_model") or data_cfg.get("fill_model", "next_open")
        self.signal_logger = SignalLogger(
            signals_cfg.get("log_dir", "data/signals"),
            retention_days=signals_cfg.get("retention_days"),
            fill_model=self.fill_model,
            slippage_bps=data_cfg.get("slippage_bps", 5),
        )
        self.snapshots_dir = config["paths"]["snapshots"]
        sizing_cfg = config.get("position_sizing", {})
        self.top_n = sizing_cfg.get("top_n", 3)
        self.exit_rank_threshold = sizing_cfg.get("exit_rank_threshold", 5)
        self.min_trade_threshold = sizing_cfg.get("min_trade_threshold", 0.02)
        # Equal-weight targets must fit under the position cap or every
        # rebalance fails validation.
        if self.top_n > 0 and 1.0 / self.top_n > self.risk_manager.max_position_pct + 1e-9:
            logger.warning(
                f"top_n={self.top_n} implies a {100.0/self.top_n:.1f}% target, above "
                f"risk_limits.max_position_pct={self.risk_manager.max_position_pct*100:.1f}% — "
                "validation will reject every rebalance"
            )
        # Load paper portfolio for live fills
        try:
            self.portfolio = PaperPortfolio.load_latest(self.snapshots_dir)
        except Exception:  # noqa: BLE001
            self.portfolio = None
        mode = "DRY RUN" if dry_run else "PAPER-LIVE"
        logger.info(f"ExecutionEngine initialized in {mode} mode")

    def run(self) -> ExecutionReport:
        timestamp = datetime.now(timezone.utc)
        logger.info(f"Starting execution pipeline at {timestamp.isoformat()}")
        self.risk_manager.check_kill_switch()

        try:
            # 1. Signal
            logger.info("Step 1: Generating momentum signal...")
            signal = generate_momentum_signal(top_n=self.top_n)
            rankings = signal["ranked"].to_dict(orient="records")
            target_weights = signal["target_weights"]
            logger.info(f"Top {self.top_n}: {signal['top_sectors']}")

            # 2. Snapshot
            logger.info("Step 2: Loading paper snapshot...")
            snapshot = load_latest_snapshot(self.snapshots_dir)
            if snapshot is None:
                raise ValueError("No portfolio snapshot found. Run 'python main.py seed' first.")

            # Stale guard: 48h
            ts_str = snapshot.get("timestamp", "")
            if ts_str:
                try:
                    snap_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    age_h = (datetime.now(timezone.utc) - snap_dt).total_seconds() / 3600
                    if age_h > 48:
                        raise ValueError(f"Snapshot is {age_h:.0f}h old ({ts_str}). Max 48h. Run 'python main.py snapshot' to refresh.")
                    if age_h > 24:
                        logger.warning(f"Snapshot is {age_h:.0f}h old — consider refreshing")
                except ValueError:
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Could not parse snapshot ts {ts_str}: {e}")

            total_equity = float(snapshot.get("total_equity", 0))
            cash = float(snapshot.get("cash", 0))
            current_weights = get_current_weights(snapshot)

            current_positions: dict[str, dict] = {}
            for pos in snapshot.get("positions", []):
                sym = pos.get("symbol")
                if sym:
                    current_positions[sym] = pos

            # Reload portfolio object fresh from file for live apply
            if not self.dry_run:
                self.portfolio = PaperPortfolio.load_latest(self.snapshots_dir) or PaperPortfolio(cash=cash, positions={k: {"quantity": float(v.get("quantity",0)), "avg_cost": float(v.get("avg_cost",0))} for k,v in current_positions.items()}, snapshots_dir=self.snapshots_dir)
            else:
                self.portfolio = None

            # 3. Prices
            logger.info("Step 3: Fetching current prices...")
            all_symbols = list(set(target_weights.keys()) | set(current_positions.keys()))
            from src.broker.yfinance_provider import get_current_prices
            current_prices = get_current_prices(all_symbols)

            missing_prices = [
                s for s in target_weights
                if float(target_weights[s]) > 0 and s not in current_prices
            ]
            if missing_prices:
                raise ValueError(
                    f"No price data for target symbols {missing_prices} — refusing to trade a "
                    "partial portfolio. Retry once yfinance responds."
                )

            # 4. Sized trades
            logger.info("Step 4: Calculating trades...")
            current_position_values = {sym: float(pos.get("market_value", 0)) for sym, pos in current_positions.items()}
            position_sizer = PositionSizer(total_equity=total_equity, cash_available=cash, current_positions=current_positions, config=self.config)
            sized_trades = position_sizer.generate_trades(target_weights=target_weights, current_prices=current_prices, min_threshold=self.min_trade_threshold)
            trades = [
                {"symbol": t.symbol, "action": t.action, "shares": t.shares, "price": t.price,
                 "target_weight": t.target_weight, "current_weight": t.current_weight, "trade_value": t.trade_value, "reason": t.reason}
                for t in sized_trades
            ]

            # 5. Validate
            logger.info("Step 5: Validating trades...")
            validation = self.risk_manager.validate_batch(trades=trades, total_equity=total_equity, current_positions=current_position_values)
            validation_dict = {"valid": validation.valid, "total_turnover_pct": validation.total_turnover_pct, "rejected_count": validation.rejected_count, "reason": validation.reason}
            if not validation.valid:
                logger.warning(f"Validation failed: {validation.reason}")
                reasoning = self._generate_reasoning(rankings, current_positions, trades, validation)
                self.signal_logger.log_signal(signal=signal, trades=trades, execution_mode="dry_run" if self.dry_run else "live", validation_result=validation_dict, execution_results=None, reasoning=reasoning)
                return ExecutionReport(
                    timestamp=timestamp.isoformat(), execution_mode="dry_run" if self.dry_run else "live",
                    signal_date=signal["signal_date"], rankings=rankings, top_sectors=signal["top_sectors"],
                    target_weights=target_weights, current_weights=current_weights, trades=trades,
                    validation_result=validation_dict, execution_results=[], pending=[],
                    total_equity=total_equity, cash=cash,
                    reasoning=reasoning, success=False, error_message=f"Validation failed: {validation.reason}"
                )

            # 6. Execute
            logger.info(f"Step 6: Executing {len(trades)} trades [{'DRY RUN' if self.dry_run else 'PAPER-LIVE'}]...")
            pending: list[str] = []
            if not trades:
                execution_results = []
            else:
                broker = PaperBroker(self.config, dry_run=self.dry_run, portfolio=self.portfolio)
                results = broker.submit_batch(trades, signal_date=snapshot.get("asof_close"))
                pending = [r.symbol for r in results if r.status == "pending"]
                execution_results = [
                    {"order_id": r.order_id, "symbol": r.symbol, "action": r.action, "shares": r.shares,
                     "order_type": r.order_type, "status": r.status, "fill_price": r.fill_price, "fill_time": r.fill_time,
                     "message": r.message, "dry_run": r.dry_run}
                    for r in results
                ]

            # 7. Log
            logger.info("Step 7: Logging signal...")
            reasoning = self._generate_reasoning(rankings, current_positions, trades, validation)
            self.signal_logger.log_signal(signal=signal, trades=trades, execution_mode="dry_run" if self.dry_run else "live", validation_result=validation_dict, execution_results=execution_results, reasoning=reasoning)
            mode_str = "DRY RUN" if self.dry_run else "PAPER-LIVE"
            rejected = [r["symbol"] for r in execution_results if r.get("status") == "rejected"]
            error_message = ""
            if pending:
                error_message = (
                    f"{len(pending)} fill(s) pending the next open: {', '.join(pending)}. "
                    "Re-run 'execute --live' after the next session opens."
                )
            elif rejected:
                error_message = f"{len(rejected)} fill(s) rejected: {', '.join(rejected)}"
            logger.info(
                f"Execution complete [{mode_str}]: {len(trades)} trades, "
                f"pending={len(pending)}, rejected={len(rejected)}"
            )
            return ExecutionReport(
                timestamp=timestamp.isoformat(), execution_mode="dry_run" if self.dry_run else "live",
                signal_date=signal["signal_date"], rankings=rankings, top_sectors=signal["top_sectors"],
                target_weights=target_weights, current_weights=current_weights, trades=trades,
                validation_result=validation_dict, execution_results=execution_results, pending=pending,
                total_equity=total_equity, cash=cash, reasoning=reasoning,
                success=not pending and not rejected, error_message=error_message
            )

        except KillSwitchActive:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Execution pipeline failed: {e}")
            return ExecutionReport(
                timestamp=timestamp.isoformat(), execution_mode="dry_run" if self.dry_run else "live",
                signal_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                rankings=[], top_sectors=[], target_weights={}, current_weights={}, trades=[],
                validation_result=None, execution_results=[], pending=[], total_equity=0, cash=0,
                reasoning="", success=False, error_message=str(e)
            )

    def _generate_reasoning(self, rankings, current_positions, trades, validation) -> str:  # type: ignore[no-untyped-def]
        current_holdings = list(current_positions.keys())
        buys = [t["symbol"] for t in trades if t["action"] == "BUY"]
        sells = [t["symbol"] for t in trades if t["action"] == "SELL"]
        return format_signal_reasoning(rankings=rankings, current_holdings=current_holdings, buys=buys, sells=sells, exit_threshold=self.exit_rank_threshold)


def run_execution_pipeline(config: dict, dry_run: bool = True) -> ExecutionReport:
    engine = ExecutionEngine(config, dry_run=dry_run)
    return engine.run()


def format_execution_report(report: ExecutionReport) -> str:
    lines = [
        f"EXECUTION REPORT - {report.signal_date}",
        "=" * 60,
        f"Mode: {report.execution_mode.upper()}{' (PAPER)' if report.execution_mode=='live' else ''}",
        f"Timestamp: {report.timestamp}",
        f"Status: {'SUCCESS' if report.success else 'FAILED'}",
        "",
    ]
    if report.error_message:
        lines.append(f"ERROR: {report.error_message}")
        lines.append("")
    lines.extend([f"Total Equity: ${report.total_equity:,.2f}", f"Cash: ${report.cash:,.2f}", "", f"Top Sectors: {', '.join(report.top_sectors) if report.top_sectors else 'NONE'}", ""])
    lines.append("Sector Rankings:")
    lines.append("-" * 40)
    for r in report.rankings:
        symbol = r.get("symbol", "")
        mom = r.get("momentum_12_1", 0)
        rank = r.get("rank", 0)
        weight = r.get("target_weight", 0)
        lines.append(f" {rank}. {symbol}: {mom*100:+.1f}% (target: {weight*100:.0f}%)")
    lines.append("")
    lines.append(f"Trades ({len(report.trades)}):")
    lines.append("-" * 40)
    if not report.trades:
        lines.append(" No trades")
    else:
        for t in report.trades:
            lines.append(f" {t['action']} {t['shares']} {t['symbol']} @ ${t['price']:.2f} (${t['trade_value']:,.0f}) — {t.get('reason','')}")
    if report.validation_result:
        lines.extend(["", "Validation:", f" Status: {'PASSED' if report.validation_result['valid'] else 'FAILED'}", f" Turnover: {report.validation_result['total_turnover_pct']*100:.1f}%", f" Message: {report.validation_result['reason']}"])
    if report.execution_results:
        lines.extend(["", "Execution Results:", "-" * 40])
        for r in report.execution_results:
            status = r.get("status", "")
            symbol = r.get("symbol", "")
            action = r.get("action", "")
            shares = r.get("shares", 0)
            fill = r.get("fill_price", "")
            msg = r.get("message", "")
            fill_str = f" @ ${fill:.2f}" if fill else ""
            lines.append(f" [{status.upper()}] {action} {shares} {symbol}{fill_str} — {msg}")
        lines.append("")
    if report.pending:
        lines.extend(["", "Pending (waiting for the next open):", "-" * 40])
        for symbol in report.pending:
            lines.append(f" {symbol}: not filled — re-run 'execute --live' after the session opens")
        lines.append("")
    lines.append("Data source: Yahoo Finance (paper fills) — simulated, not broker-verified")
    lines.append("")
    return "\n".join(lines)
