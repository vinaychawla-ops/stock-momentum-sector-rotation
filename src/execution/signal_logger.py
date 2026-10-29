"""Signal audit logger — one JSON file per pipeline run.

Filenames are ``YYYY-MM-DD_HHMMSS_<mode>.json`` so a dry run and a live run on
the same day both survive. ``retention_days`` prunes old files; ``0``/``None``
keeps everything.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def format_signal_reasoning(rankings: list[dict], current_holdings: list[str], buys: list[str], sells: list[str], exit_threshold: int = 5) -> str:
    lines = []
    top = [r.get("symbol","") for r in sorted(rankings, key=lambda x: x.get("rank", 99))[:3]]
    lines.append(f"Top sectors: {', '.join(top)}")
    lines.append(f"Held: {', '.join(current_holdings) if current_holdings else 'none'}")
    if buys:
        lines.append(f"Buys: {', '.join(buys)}")
    if sells:
        lines.append(f"Sells: {', '.join(sells)}")
    if not buys and not sells:
        lines.append("No trades — within threshold.")
    lines.append(f"Exit threshold rank > {exit_threshold}.")
    return "\n".join(lines)


class SignalLogger:
    def __init__(
        self,
        log_dir: str = "data/signals",
        retention_days: Optional[int] = None,
        fill_model: Optional[str] = None,
        slippage_bps: Optional[float] = None,
    ):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = int(retention_days) if retention_days else 0
        self.fill_model = fill_model
        self.slippage_bps = slippage_bps

    def log_signal(self, signal: dict, trades: list[dict], execution_mode: str, validation_result: Optional[dict] = None, execution_results: Optional[list[dict]] = None, reasoning: str = "") -> Path:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        ts = now.isoformat().replace("+00:00", "Z")

        rankings = signal.get("ranked")
        if hasattr(rankings, "to_dict"):
            try:
                rankings = rankings.to_dict(orient="records")
            except Exception:  # noqa: BLE001
                rankings = []

        payload = {
            "timestamp": ts,
            "signal_date": signal.get("signal_date", date_str),
            "file_date": date_str,
            "execution_mode": execution_mode,
            "top_sectors": signal.get("top_sectors", []),
            "target_weights": signal.get("target_weights", {}),
            "rankings": rankings or [],
            "trades": trades or [],
            "validation": validation_result or {},
            "execution_results": execution_results or [],
            "reasoning": reasoning,
            "data_source": "yfinance",
            "fill_model": self.fill_model,
            "slippage_bps": self.slippage_bps,
        }

        file_path = self._unique_path(date_str, execution_mode)
        tmp = file_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str))
        tmp.replace(file_path)
        logger.info(f"Logged signal to {file_path} ({execution_mode}, {len(trades)} trades)")
        self._prune(date_str)
        return file_path

    def _unique_path(self, date_str: str, execution_mode: str) -> Path:
        base = f"{date_str}_{datetime.now(timezone.utc).strftime('%H%M%S')}_{execution_mode}"
        candidate = self.log_dir / f"{base}.json"
        suffix = 1
        while candidate.exists():
            candidate = self.log_dir / f"{base}-{suffix}.json"
            suffix += 1
        return candidate

    def _prune(self, today_str: str) -> None:
        if self.retention_days <= 0:
            return
        cutoff = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=self.retention_days)).date()
        for f in self.log_dir.glob("*.json"):
            try:
                file_date = datetime.strptime(f.stem[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    f.unlink()
                    logger.info(f"Pruned old signal log {f.name}")
                except OSError as e:  # noqa: BLE001
                    logger.warning(f"Could not prune {f}: {e}")

    def get_signals_history(self, limit: int = 30) -> list[dict]:
        files = sorted(self.log_dir.glob("*.json"), reverse=True)
        out: list[dict] = []
        for f in files:
            if len(out) >= limit:
                break
            try:
                data = json.loads(f.read_text())
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to load signal {f}: {e}")
                continue
            # Legacy single-day files could hold a list; flatten them.
            if isinstance(data, list):
                out.extend(item for item in data if isinstance(item, dict))
            elif isinstance(data, dict):
                out.append(data)
        return out[:limit]
