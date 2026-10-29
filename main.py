#!/usr/bin/env python3
"""CLI — US Momentum Paper (Yahoo Finance). No IBKR."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from src.logging_setup import setup_logging


# Ensure config exists before commands that load it
def load_config() -> dict:
    p = Path("config/config.yaml")
    if not p.exists():
        print("Error: config/config.yaml not found.")
        print("Copy config/config.example.yaml to config/config.yaml and update settings.")
        sys.exit(1)
    with open(p) as f:
        config = yaml.safe_load(f) or {}
    setup_logging(config)
    return config


def cmd_seed(args: argparse.Namespace) -> None:
    from src.portfolio.paper_portfolio import PaperPortfolio
    config = load_config()
    snapshots_dir = config["paths"]["snapshots"]
    csv_dir = config["paths"]["executions"]
    # Check if already seeded
    existing = sorted(Path(snapshots_dir).glob("*.json"))
    if existing and not args.force:
        print(f"Snapshot already exists: {existing[-1].name}")
        print("Use --force to overwrite or --cash to set different cash.")
        print(f"Cash in latest snapshot will be used; to reset, use: python main.py seed --force --cash {args.cash}")
        return
    initial_cash = float(args.cash or config.get("paper", {}).get("initial_cash", 100000))
    print(f"Seeding paper portfolio with ${initial_cash:,.2f} cash...")
    Path(snapshots_dir).mkdir(parents=True, exist_ok=True)
    Path(csv_dir).mkdir(parents=True, exist_ok=True)
    portfolio = PaperPortfolio.seed(snapshots_dir, initial_cash=initial_cash, currency=config.get("paper", {}).get("currency", "USD"))
    fill_model = config.get("data", {}).get("fill_model", "next_open")
    path = portfolio.save_snapshot({}, fill_model=fill_model)
    print(f"Seeded: {path}")
    print(f"Equity ${initial_cash:,.2f} — all cash. Next: python main.py signal")


def cmd_snapshot(args: argparse.Namespace) -> None:
    from src.portfolio.paper_portfolio import PaperPortfolio
    config = load_config()
    snapshots_dir = config["paths"]["snapshots"]
    portfolio = PaperPortfolio.load_latest(snapshots_dir)
    if portfolio is None:
        print("No existing snapshot. Run 'python main.py seed' first.")
        sys.exit(1)
    fill_model = config.get("data", {}).get("fill_model", "next_open")
    print("Marking paper portfolio to market at yfinance close...")
    try:
        path = portfolio.save_snapshot_with_prices(fill_model=fill_model)
        print(f"Snapshot saved to: {path}")
        import json
        snap = json.loads(path.read_text())
        print(f"Equity ${snap['total_equity']:,.2f} | Cash ${snap['cash']:,.2f} | Positions {len(snap['positions'])} | {snap['timestamp']}")
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        sys.exit(1)


def cmd_pull(args: argparse.Namespace) -> None:
    print("`pull` is deprecated (was IBKR fills). For paper trading, snapshots are mark-to-market via yfinance.")
    print("Use: python main.py snapshot   # to mark portfolio at close")
    print("     python main.py execute --live  # to simulate fills")
    # Alias to snapshot for convenience
    cmd_snapshot(args)


def cmd_annotate(args: argparse.Namespace) -> None:
    from src.annotations import interactive_annotate, list_annotations, format_annotation_list
    config = load_config()
    annotations_dir = config["paths"]["annotations"]
    if args.list:
        print(format_annotation_list(list_annotations(annotations_dir)))
        return
    trade_id = args.trade_id if args.trade_id != "new" else None
    interactive_annotate(annotations_dir=annotations_dir, trade_id=trade_id, pre_trade=args.pre or (not args.post), post_trade=args.post)


def cmd_report(args: argparse.Namespace) -> None:
    from src.export import generate_monthly_report
    config = load_config()
    print(f"Generating report for {args.month}...")
    try:
        path = generate_monthly_report(args.month, config["paths"]["snapshots"], config["paths"]["executions"], config["paths"]["annotations"], config["paths"]["reports"])
        print(f"Report saved to: {path}")
    except Exception as e:  # noqa: BLE001
        print(f"Error generating report: {e}")
        sys.exit(1)


def cmd_weekly_report(args: argparse.Namespace) -> None:
    from src.export import generate_weekly_report, get_current_week
    config = load_config()
    week = args.week if args.week else get_current_week()
    print(f"Generating weekly report for {week}...")
    try:
        path = generate_weekly_report(week, config["paths"]["snapshots"], config["paths"]["executions"], config["paths"]["annotations"], config["paths"]["reports"])
        print(f"Report saved to: {path}")
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        sys.exit(1)


def cmd_run_job(args: argparse.Namespace) -> None:
    from src.scheduler import run_job_now
    config = load_config()
    print(f"Running job: {args.job}...")
    try:
        run_job_now(config, args.job)
        print("Done.")
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        sys.exit(1)


def cmd_stats(args: argparse.Namespace) -> None:
    from src.performance import compute_all_metrics, format_performance_report
    config = load_config()
    try:
        metrics = compute_all_metrics(config["paths"]["snapshots"], config["paths"]["executions"], start_date=args.start, end_date=args.end)
        print(format_performance_report(metrics))
    except Exception as e:  # noqa: BLE001
        print(f"Error computing stats: {e}")
        sys.exit(1)


def cmd_slippage(args: argparse.Namespace) -> None:
    from src.slippage_analyzer import analyze_slippage, format_slippage_report
    config = load_config()
    try:
        analysis = analyze_slippage(config["paths"]["executions"], start_date=args.start, end_date=args.end, outlier_threshold_bps=args.threshold)
        print(format_slippage_report(analysis, outlier_threshold_bps=args.threshold))
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        sys.exit(1)


def cmd_signal(args: argparse.Namespace) -> None:
    from src.signals.momentum import generate_momentum_signal, format_signal_report
    from src.signals.rebalance import load_latest_snapshot
    config = load_config()
    print("Generating US 12-1 momentum signal (Yahoo Finance)...")
    try:
        signal = generate_momentum_signal(top_n=args.top_n)
        snapshot = load_latest_snapshot(config["paths"]["snapshots"])
        cash = snapshot.get("cash", 0) if snapshot else 0
        print(format_signal_report(signal, cash=cash))
    except Exception as e:  # noqa: BLE001
        print(f"Error generating signal: {e}")
        sys.exit(1)


def cmd_rebalance(args: argparse.Namespace) -> None:
    from src.signals.rebalance import generate_rebalance_trades, format_rebalance_report
    config = load_config()
    print("Generating rebalance trades (paper)...")
    print()
    try:
        rebalance = generate_rebalance_trades(config["paths"]["snapshots"], top_n=args.top_n, min_threshold=args.threshold)
        print(format_rebalance_report(rebalance))
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        sys.exit(1)


def cmd_dashboard(args: argparse.Namespace) -> None:
    from src.dashboard import run_dashboard
    config = load_config()
    run_dashboard(config, host=args.host, port=args.port)


def cmd_scheduler(args: argparse.Namespace) -> None:
    from src.scheduler import run_scheduler
    config = load_config()
    health_port = args.health_port or config.get("scheduler", {}).get("health_port", 8080)
    run_scheduler(config, health_port=health_port)


def cmd_execute(args: argparse.Namespace) -> None:
    from src.execution.engine import ExecutionEngine, format_execution_report
    from src.execution.risk_manager import KillSwitchActive
    config = load_config()
    dry_run = not args.live
    mode_str = "DRY RUN" if dry_run else "PAPER-LIVE"
    if not dry_run:
        print("=" * 60)
        print("PAPER-LIVE MODE - fills are SIMULATED via Yahoo Finance")
        print(f"Fill model: {config.get('data',{}).get('fill_model','next_open')} + {config.get('data',{}).get('slippage_bps',5)} bps slippage")
        print("No brokerage orders are sent. Executions will be written to data/executions/")
        print("=" * 60)
        confirm = input("Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            print("Aborted.")
            sys.exit(0)
    print(f"Running execution pipeline [{mode_str}]...")
    print()
    try:
        engine = ExecutionEngine(config, dry_run=dry_run)
        report = engine.run()
        print(format_execution_report(report))
        if report.success:
            print(f"Execution complete: {len(report.trades)} trades [{mode_str}]")
        elif report.pending:
            print(f"{len(report.pending)} fill(s) pending the next open: {', '.join(report.pending)}")
            print("Re-run 'python main.py execute --live' after the next session opens (09:30 ET).")
            sys.exit(2)
        else:
            print(f"Execution failed: {report.error_message}")
            sys.exit(1)
    except KillSwitchActive as e:
        print(f"BLOCKED: {e}")
        print("Deactivate kill switch to enable trading.")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        sys.exit(1)


def cmd_kill_switch(args: argparse.Namespace) -> None:
    from src.execution.risk_manager import RiskManager
    config = load_config()
    rm = RiskManager(config)
    if args.action == "status":
        if rm.is_kill_switch_active():
            print(f"Kill switch: ACTIVE\nReason: {rm.get_kill_switch_reason()}")
        else:
            print("Kill switch: INACTIVE\nTrading is enabled.")
    elif args.action == "activate":
        reason = args.reason or "Manual activation"
        rm.activate_kill_switch(reason)
        print(f"Kill switch ACTIVATED: {reason}\nAll paper trading is now blocked.")
    elif args.action == "deactivate":
        if not rm.is_kill_switch_active():
            print("Kill switch is already inactive.")
            return
        print("WARNING: Deactivating kill switch will enable trading.")
        confirm = input("Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            print("Aborted.")
            return
        rm.deactivate_kill_switch()
        print("Kill switch DEACTIVATED. Trading is now enabled.")


def main() -> None:
    parser = argparse.ArgumentParser(description="US Sector Momentum - Paper Trading (Yahoo Finance / NYSE)", formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""
Examples:
  python main.py seed --cash 100000     Seed paper portfolio (first run)
  python main.py snapshot              Mark-to-market at yfinance close
  python main.py signal                12-1 momentum ranking (XL*)
  python main.py rebalance             Current vs target weights
  python main.py execute               Dry-run (no fills)
  python main.py execute --live        Paper fills (simulated, writes executions)
  python main.py stats                 Performance (Sharpe, DD, win rate)
  python main.py slippage              Modeled slippage
  python main.py report 2026-08        Monthly PDF (USD, paper disclaimer)
  python main.py dashboard --port 5050 Web UI
  python main.py scheduler             Exchange-time scheduler (NYSE calendar)
  python main.py kill-switch status    Kill switch
""")
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="Seed paper portfolio with initial cash")
    p_seed.add_argument("--cash", type=float, default=None, help="Initial cash USD (default from config paper.initial_cash)")
    p_seed.add_argument("--force", action="store_true", help="Overwrite existing snapshot")

    p_snap = sub.add_parser("snapshot", help="Save paper portfolio snapshot (mark-to-market)")
    p_pull = sub.add_parser("pull", help="Deprecated alias for snapshot (was IBKR)")
    p_annotate = sub.add_parser("annotate", help="Trade thesis notes")
    p_annotate.add_argument("trade_id", nargs="?", default="new", help="Trade ID or 'new'")
    p_annotate.add_argument("--pre", action="store_true", help="Pre-trade annotation")
    p_annotate.add_argument("--post", action="store_true", help="Post-trade annotation")
    p_annotate.add_argument("--list", action="store_true", help="List annotations")

    p_report = sub.add_parser("report", help="Generate monthly PDF report")
    p_report.add_argument("month", help="YYYY-MM e.g. 2026-08")
    p_wreport = sub.add_parser("weekly-report", help="Generate weekly PDF report")
    p_wreport.add_argument("week", nargs="?", default=None, help="YYYY-Www e.g. 2026-W35")
    p_runjob = sub.add_parser("run-job", help="Manually run a scheduler job")
    p_runjob.add_argument("job", help="snapshot | execute | weekly_report")

    p_stats = sub.add_parser("stats", help="Performance summary")
    p_stats.add_argument("start", nargs="?", default=None, help="Start date YYYY-MM-DD")
    p_stats.add_argument("end", nargs="?", default=None, help="End date YYYY-MM-DD")
    p_slip = sub.add_parser("slippage", help="Slippage analysis (modeled)")
    p_slip.add_argument("start", nargs="?", default=None)
    p_slip.add_argument("end", nargs="?", default=None)
    p_slip.add_argument("--threshold", type=float, default=10, help="Outlier threshold bps")

    p_signal = sub.add_parser("signal", help="12-1 momentum signal (US SPDRs)")
    p_signal.add_argument("--top-n", type=int, default=3, help="Top N sectors")

    p_rebal = sub.add_parser("rebalance", help="Rebalance trade list")
    p_rebal.add_argument("--top-n", type=int, default=3)
    p_rebal.add_argument("--threshold", type=float, default=0.02, help="Min weight diff")

    p_exec = sub.add_parser("execute", help="Execution pipeline (paper)")
    p_exec.add_argument("--live", action="store_true", help="Paper-live: simulate fills and write executions")

    p_kill = sub.add_parser("kill-switch", help="Emergency halt (paper)")
    p_kill.add_argument("action", choices=["status","activate","deactivate"])
    p_kill.add_argument("reason", nargs="?", default=None)

    p_dash = sub.add_parser("dashboard", help="Start web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int, default=5050)

    p_sched = sub.add_parser("scheduler", help="Start ET-aware scheduler")
    p_sched.add_argument("--health-port", type=int, default=None)

    args = parser.parse_args()

    dispatch = {
        "seed": cmd_seed,
        "snapshot": cmd_snapshot,
        "pull": cmd_pull,
        "annotate": cmd_annotate,
        "report": cmd_report,
        "weekly-report": cmd_weekly_report,
        "run-job": cmd_run_job,
        "stats": cmd_stats,
        "slippage": cmd_slippage,
        "signal": cmd_signal,
        "rebalance": cmd_rebalance,
        "execute": cmd_execute,
        "kill-switch": cmd_kill_switch,
        "dashboard": cmd_dashboard,
        "scheduler": cmd_scheduler,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
