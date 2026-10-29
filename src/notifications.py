"""Email notifications — optional, reuses original logic (kept minimal)."""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def _send_email(config: dict, subject: str, body: str) -> bool:
    email_cfg = config.get("email", {})
    if not email_cfg.get("enabled"):
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = email_cfg["sender_email"]
        msg["To"] = email_cfg["recipient_email"]
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(email_cfg["smtp_host"], int(email_cfg.get("smtp_port", 587))) as server:
            server.starttls()
            server.login(email_cfg["sender_email"], email_cfg["sender_password"])
            server.send_message(msg)
        logger.info(f"Email sent: {subject}")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Email failed: {e}")
        return False


def send_daily_summary(config: dict) -> bool:
    from src.performance import compute_all_metrics
    try:
        metrics = compute_all_metrics(config["paths"]["snapshots"], config["paths"]["executions"])
        body = (
            f"Daily Summary — US Momentum (Paper)\n"
            f"Equity: ${metrics['equity'].iloc[-1]:,.2f} (if any)\n"
            f"Total Return: {metrics['total_return']*100:+.2f}%\n"
            f"Sharpe: {metrics['sharpe']:.2f}\n"
            f"Trades: {metrics['num_trades']}\n"
        )
        return _send_email(config, "Daily Summary — US Momentum", body)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Daily summary failed: {e}")
        return False
