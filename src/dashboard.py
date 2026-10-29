"""Flask dashboard — US/paper variant (USD, NYSE, paper banner)."""
from __future__ import annotations

import html
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from flask import Flask, render_template

from src.performance import load_snapshots, compute_equity_curve, total_return
from src.signals.momentum import generate_momentum_signal, display_symbol, SECTOR_NAMES
from src.execution.signal_logger import SignalLogger
from src.execution.risk_manager import RiskManager

logger = logging.getLogger(__name__)


def create_app(config: dict) -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).parent.parent / "templates"))
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", os.urandom(32).hex())

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "font-src 'self' https://cdn.jsdelivr.net;"
        )
        return response

    @app.route("/")
    def dashboard():
        data = get_dashboard_data(config)
        return render_template("dashboard.html", **data)

    @app.route("/signals")
    def signals():
        data = get_signals_data(config)
        return render_template("signals.html", **data)

    return app


def load_latest_snapshot(snapshots_dir: str) -> Optional[dict]:
    snap_path = Path(snapshots_dir)
    if not snap_path.exists():
        return None
    files = sorted(snap_path.glob("*.json"), reverse=True)
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f)


def get_equity_chart_data(snapshots_dir: str) -> dict:
    snapshots = load_snapshots(snapshots_dir)
    if snapshots.empty:
        return {"dates": [], "values": []}
    equity = compute_equity_curve(snapshots)
    return {"dates": [d.strftime("%Y-%m-%d") for d in equity.index], "values": [round(float(v), 2) for v in equity.values]}


def get_signal_data() -> list[dict]:
    try:
        signal = generate_momentum_signal()
        rows = []
        for _, row in signal["ranked"].iterrows():
            symbol = row["symbol"]
            rows.append({"rank": int(row["rank"]), "symbol": display_symbol(symbol), "sector": SECTOR_NAMES.get(symbol, ""), "momentum": float(row["momentum_12_1"]), "target_weight": float(row["target_weight"])})
        return rows
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to generate signal: {e}")
        return []


def calculate_days_to_rebalance() -> int:
    # Weekly rebalance: next Sunday (NYSE week) — keep simple; monthly signal also on 1st trading day
    today = date.today()
    return (6 - today.weekday()) % 7


def get_dashboard_data(config: dict) -> dict:
    snapshots_dir = config["paths"]["snapshots"]
    snapshot = load_latest_snapshot(snapshots_dir)
    if snapshot:
        total_equity = snapshot.get("total_equity", 0)
        cash = snapshot.get("cash", 0)
        positions = snapshot.get("positions", [])
        snapshot_timestamp = snapshot.get("timestamp", "Unknown")
        for pos in positions:
            pos["weight"] = pos.get("market_value", 0) / total_equity if total_equity else 0
    else:
        total_equity = 0; cash = 0; positions = []; snapshot_timestamp = "No snapshot available"

    snapshots_df = load_snapshots(snapshots_dir)
    if len(snapshots_df) >= 2:
        today_pnl = float(snapshots_df["total_equity"].iloc[-1] - snapshots_df["total_equity"].iloc[-2])
        starting = float(snapshots_df["total_equity"].iloc[0])
        total_pnl = float(total_equity - starting)
        total_ret = (total_equity / starting - 1) * 100 if starting else 0
    else:
        today_pnl = 0; total_pnl = 0; total_ret = 0

    equity_data = get_equity_chart_data(snapshots_dir)
    signal_data = get_signal_data()
    days_to_rebalance = calculate_days_to_rebalance()

    def sanitize(val):
        return html.escape(val) if isinstance(val, str) else val

    return {
        "total_equity": total_equity, "cash": cash, "today_pnl": today_pnl, "total_pnl": total_pnl,
        "total_return": total_ret, "positions": positions, "equity_data": json.dumps(equity_data),
        "signal_data": signal_data, "days_to_rebalance": days_to_rebalance,
        "snapshot_timestamp": sanitize(snapshot_timestamp), "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": snapshot.get("data_source", "yfinance") if snapshot else "yfinance",
        "fill_model": snapshot.get("fill_model", config.get("data", {}).get("fill_model", "next_open")) if snapshot else config.get("data", {}).get("fill_model", "next_open"),
    }


def get_signals_data(config: dict) -> dict:
    signals_dir = config.get("signals", {}).get("log_dir", "data/signals")
    sl = SignalLogger(signals_dir)
    signals = sl.get_signals_history(limit=30)
    risk_manager = RiskManager(config)
    kill_switch_active = risk_manager.is_kill_switch_active()
    kill_switch_reason = risk_manager.get_kill_switch_reason()
    exec_mode = config.get("execution", {}).get("mode", "dry_run")
    formatted = []
    for sig in signals:
        trades = sig.get("trades", [])
        buy_count = sum(1 for t in trades if t.get("action") == "BUY")
        sell_count = sum(1 for t in trades if t.get("action") == "SELL")
        exec_results = sig.get("execution_results", [])
        executed = sum(1 for r in exec_results if r.get("status") in ("submitted","filled","dry_run"))
        formatted.append({
            "date": sig.get("signal_date", sig.get("file_date","Unknown")),
            "timestamp": sig.get("timestamp",""),
            "mode": sig.get("execution_mode","unknown"),
            "top_sectors": sig.get("top_sectors", []),
            "trade_count": len(trades), "buy_count": buy_count, "sell_count": sell_count,
            "executed_count": executed,
            "validation_status": "Passed" if sig.get("validation",{}).get("valid", False) else "Failed",
            "trades": trades, "rankings": sig.get("rankings", []),
        })
    return {"signals": formatted, "signal_count": len(formatted), "kill_switch_active": kill_switch_active, "kill_switch_reason": kill_switch_reason, "execution_mode": exec_mode, "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def run_dashboard(config: dict, host: str = "127.0.0.1", port: int = 5050) -> None:
    app = create_app(config)
    print(f"Starting dashboard at http://{host}:{port}  [Paper — Yahoo Finance · NYSE]")
    print("Press Ctrl+C to stop")
    app.run(host=host, port=port, debug=False)
