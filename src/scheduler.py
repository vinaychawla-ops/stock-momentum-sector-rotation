"""Scheduler — exchange-time aware (default America/New_York) + XNYS calendar.

Schedule model
--------------
A single tick runs every 30 seconds and compares the exchange wall clock
against the configured times. Each (date, time, job) fires at most once, so a
long-running job cannot be triggered twice.

Jobs:
  * snapshot  — every trading day at ``scheduler.snapshot_time``
  * execute   — governed by ``scheduler.rebalance_cadence``:
                ``monthly`` (default, first trading day), ``weekly``
                (``rebalance_day``), ``daily``, or ``off``, at ``execute_time``
  * weekly report — ``rebalance_day`` (default Sunday) at ``report_time``

There is no host-local ``schedule.at()`` registration any more; that duplicate
fired a second time on any host whose local timezone was not the exchange zone.
"""
from __future__ import annotations

import logging
import threading
import time
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import schedule

try:
    import pandas_market_calendars as mcal
    _XNYS = mcal.get_calendar("XNYS")
except Exception:  # noqa: BLE001
    _XNYS = None

logger = logging.getLogger(__name__)

DEFAULT_TZ = "America/New_York"


def _tz(config: Optional[dict] = None) -> ZoneInfo:
    name = (config or {}).get("timezone") or DEFAULT_TZ
    try:
        return ZoneInfo(str(name))
    except Exception:  # noqa: BLE001
        logger.warning(f"Unknown timezone {name!r}; using {DEFAULT_TZ}")
        return ZoneInfo(DEFAULT_TZ)


def _is_trading_day(d: Optional[datetime] = None) -> bool:
    """Check if date is an exchange trading day. Falls back to weekday."""
    if d is None:
        d = datetime.now(_tz())
    d_date = d.date()
    if _XNYS is not None:
        try:
            import pandas as pd
            sched = _XNYS.schedule(start_date=pd.Timestamp(d_date), end_date=pd.Timestamp(d_date))
            return not sched.empty
        except Exception:  # noqa: BLE001
            pass
    return d_date.weekday() < 5


def _is_first_trading_day_of_month(d: Optional[datetime] = None, tz: Optional[ZoneInfo] = None) -> bool:
    """True when `d` is the first XNYS session of its calendar month."""
    d = d or datetime.now(tz or _tz())
    target = d.date()
    if _XNYS is not None:
        try:
            import pandas as pd
            first = target.replace(day=1)
            last = target.replace(day=monthrange(target.year, target.month)[1])
            sched = _XNYS.schedule(start_date=pd.Timestamp(first), end_date=pd.Timestamp(last))
            if not sched.empty:
                return sched.index[0].date() == target
        except Exception:  # noqa: BLE001
            pass
    # Fallback: first weekday of the month
    first = target.replace(day=1)
    while first.weekday() >= 5:
        first += timedelta(days=1)
    return first == target


def _is_first_trading_day_of_week(d: Optional[datetime] = None, tz: Optional[ZoneInfo] = None) -> bool:
    """True when `d` is the first XNYS session of its ISO week."""
    d = d or datetime.now(tz or _tz())
    target = d.date()
    monday = target - timedelta(days=target.weekday())
    sunday = monday + timedelta(days=6)
    if _XNYS is not None:
        try:
            import pandas as pd
            sched = _XNYS.schedule(start_date=pd.Timestamp(monday), end_date=pd.Timestamp(sunday))
            if not sched.empty:
                return sched.index[0].date() == target
        except Exception:  # noqa: BLE001
            pass
    return target == monday


def _rebalance_due(now: datetime, sched_cfg: dict) -> bool:
    cadence = str(sched_cfg.get("rebalance_cadence", "monthly")).lower()
    if cadence == "off":
        return False
    if cadence == "daily":
        return True
    if cadence == "weekly":
        # The first *trading* day of the week, not a fixed weekday: the
        # configured rebalance_day (Sunday) is never a session, so an
        # execute bound to it could never fill.
        return _is_first_trading_day_of_week(now)
    if cadence == "monthly":
        return _is_first_trading_day_of_month(now)
    logger.warning(f"Unknown rebalance_cadence {cadence!r}; treating as monthly")
    return _is_first_trading_day_of_month(now)


def run_job_now(config: dict, job: str) -> None:
    """Manually run a named job (for CLI run-job)."""
    job = job.lower()
    if job in ("snapshot", "daily_snapshot"):
        job_snapshot(config)
    elif job in ("execute", "execute_signals"):
        job_execute(config)
    elif job in ("weekly_report", "weekly"):
        job_weekly_report(config)
    elif job in ("monthly_report",):
        from src.export import generate_monthly_report
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
        generate_monthly_report(ym, config["paths"]["snapshots"], config["paths"]["executions"], config["paths"]["annotations"], config["paths"]["reports"])
    else:
        raise ValueError(f"Unknown job: {job}. Try: snapshot, execute, weekly_report, monthly_report")


def job_snapshot(config: dict) -> None:
    if not _is_trading_day():
        logger.info("Snapshot skipped: not a trading day")
        return
    try:
        from src.portfolio.paper_portfolio import PaperPortfolio
        snapshots_dir = config["paths"]["snapshots"]
        portfolio = PaperPortfolio.load_latest(snapshots_dir)
        if portfolio is None:
            logger.warning("No existing snapshot — creating seed snapshot first")
            initial_cash = float(config.get("paper", {}).get("initial_cash", 100000))
            portfolio = PaperPortfolio.seed(snapshots_dir, initial_cash=initial_cash)
            portfolio.save_snapshot_with_prices(fill_model=config.get("data", {}).get("fill_model", "next_open"))
            logger.info(f"Seed snapshot created with ${initial_cash:,.2f}")
            return
        portfolio.save_snapshot_with_prices(fill_model=config.get("data", {}).get("fill_model", "next_open"))
        logger.info(f"Daily snapshot complete at {datetime.now(_tz(config)).isoformat()}")
        if config.get("email", {}).get("enabled"):
            try:
                from src.notifications import send_daily_summary
                send_daily_summary(config)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        logger.error(f"Snapshot job failed: {e}")


def job_execute(config: dict) -> None:
    if not _is_trading_day():
        logger.info("Execute skipped: not a trading day")
        return
    try:
        from src.execution.engine import ExecutionEngine
        dry_run = config.get("execution", {}).get("mode", "dry_run") != "live"
        engine = ExecutionEngine(config, dry_run=dry_run)
        report = engine.run()
        logger.info(
            f"Execute job complete: success={report.success} "
            f"trades={len(report.trades)} pending={len(report.pending)} "
            f"mode={report.execution_mode}"
        )
        if report.pending:
            logger.warning(
                f"{len(report.pending)} fill(s) pending the next open — "
                "run 'python main.py execute --live' after the session opens"
            )
        if config.get("email", {}).get("enabled"):
            try:
                from src.notifications import send_daily_summary
                send_daily_summary(config)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        logger.error(f"Execute job failed: {e}")


def job_weekly_report(config: dict) -> None:
    try:
        from src.export import generate_weekly_report, get_current_week
        week = get_current_week()
        path = generate_weekly_report(week, config["paths"]["snapshots"], config["paths"]["executions"], config["paths"]["annotations"], config["paths"]["reports"])
        logger.info(f"Weekly report generated: {path}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Weekly report failed: {e}")


def _start_health_server(port: int, config: Optional[dict] = None) -> None:
    def payload() -> dict:
        return {
            "status": "ok",
            "et_now": datetime.now(_tz(config)).isoformat(),
            "is_trading_day": _is_trading_day(),
            "calendar": "XNYS" if _XNYS else "weekday-fallback",
            "rebalance_cadence": str((config or {}).get("scheduler", {}).get("rebalance_cadence", "monthly")),
        }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            import json
            body = json.dumps(payload()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002
            return

    def serve():
        try:
            server = HTTPServer(("0.0.0.0", port), Handler)
            logger.info(f"Health server listening on :{port}")
            server.serve_forever()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Health server failed on :{port}: {e}")

    t = threading.Thread(target=serve, daemon=True)
    t.start()


def run_scheduler(config: dict, health_port: int = 8080) -> None:
    """Start the scheduler (exchange-time wall clock, single trigger path)."""
    from src.logging_setup import setup_logging
    setup_logging(config)

    sched_cfg = config.get("scheduler", {}) or {}
    snapshot_time = str(sched_cfg.get("snapshot_time", "16:05"))
    execute_time = str(sched_cfg.get("execute_time", "16:10"))
    report_time = str(sched_cfg.get("report_time", "21:00"))
    cadence = str(sched_cfg.get("rebalance_cadence", "monthly")).lower()
    rebalance_day = str(sched_cfg.get("rebalance_day", "sunday")).lower()
    tz = _tz(config)

    print("=" * 64)
    print("US Momentum Scheduler - NYSE")
    print(f"Timezone: {tz.key}")
    print(f"Snapshot: {snapshot_time} on trading days")
    if cadence == "monthly":
        print(f"Execute:  {execute_time} on the first trading day of each month")
    elif cadence == "weekly":
        print(f"Execute:  {execute_time} on the first trading day of each week")
    elif cadence == "daily":
        print(f"Execute:  {execute_time} every trading day")
    else:
        print("Execute:  disabled (rebalance_cadence=off)")
    print(f"Report:   Sunday {report_time}")
    print(f"Health:   http://localhost:{health_port}/")
    print(f"Now:      {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S %Z')} | trading day: {_is_trading_day()}")
    print("Press Ctrl+C to stop")
    print("=" * 64)

    _start_health_server(health_port, config)

    fired: dict[tuple[str, str, str], None] = {}

    def fire(job_name: str, when: datetime, fn) -> None:
        key = (when.strftime("%Y-%m-%d"), when.strftime("%H:%M"), job_name)
        if key in fired:
            return
        fired[key] = None
        if len(fired) > 500:
            today = when.strftime("%Y-%m-%d")
            for k in [k for k in fired if k[0] != today]:
                del fired[k]
        logger.info(f"Firing job '{job_name}' at {when.strftime('%Y-%m-%d %H:%M')} {tz.key}")
        try:
            fn(config)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Scheduled job {job_name} failed: {e}")

    def tick() -> None:
        now = datetime.now(tz)
        hm = now.strftime("%H:%M")

        if hm == snapshot_time and _is_trading_day(now):
            fire("snapshot", now, job_snapshot)

        if hm == execute_time and _is_trading_day(now) and _rebalance_due(now, sched_cfg):
            fire("execute", now, job_execute)

        if now.strftime("%A").lower() == rebalance_day and hm == report_time:
            fire("weekly_report", now, job_weekly_report)

    schedule.every().minute.do(tick)

    while True:
        schedule.run_pending()
        time.sleep(30)
