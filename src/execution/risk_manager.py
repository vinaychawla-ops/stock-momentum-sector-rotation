"""Risk manager + kill switch — unchanged semantics for paper."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Defaults mirror config/config.example.yaml so an absent key behaves like the
# shipped config, not like a surprise 25%/50% cap.
DEFAULT_MAX_POSITION_PCT = 0.35
DEFAULT_MAX_TURNOVER_PCT = 1.00


class KillSwitchActive(Exception):
    pass


@dataclass
class TradeValidation:
    valid: bool
    reason: str
    turnover_pct: float = 0.0


@dataclass
class BatchValidationResult:
    valid: bool
    total_turnover_pct: float
    rejected_count: int
    reason: str
    details: list[TradeValidation] = None  # type: ignore


class RiskManager:
    def __init__(self, config: dict):
        self.config = config
        limits = config.get("risk_limits", {}) or {}
        if "max_position_pct" not in limits:
            logger.warning(
                f"risk_limits.max_position_pct missing; using default "
                f"{DEFAULT_MAX_POSITION_PCT:.2f}"
            )
        if "max_turnover_pct" not in limits:
            logger.warning(
                f"risk_limits.max_turnover_pct missing; using default "
                f"{DEFAULT_MAX_TURNOVER_PCT:.2f}"
            )
        self.max_position_pct: float = float(limits.get("max_position_pct", DEFAULT_MAX_POSITION_PCT))
        self.max_turnover_pct: float = float(limits.get("max_turnover_pct", DEFAULT_MAX_TURNOVER_PCT))
        self.kill_switch_file = Path(limits.get("kill_switch_file", "data/.kill_switch"))

    def is_kill_switch_active(self) -> bool:
        return self.kill_switch_file.exists()

    def get_kill_switch_reason(self) -> str:
        if not self.is_kill_switch_active():
            return ""
        try:
            txt = self.kill_switch_file.read_text().strip()
            return txt or "No reason given"
        except Exception:  # noqa: BLE001
            return "Unknown"

    def check_kill_switch(self) -> None:
        if self.is_kill_switch_active():
            raise KillSwitchActive(f"Kill switch active: {self.get_kill_switch_reason()}")

    def activate_kill_switch(self, reason: str = "Manual activation") -> None:
        self.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
        self.kill_switch_file.write_text(reason)
        logger.warning(f"Kill switch ACTIVATED: {reason}")

    def deactivate_kill_switch(self) -> None:
        if self.kill_switch_file.exists():
            self.kill_switch_file.unlink()
            logger.info("Kill switch DEACTIVATED")

    def validate_trade(self, trade: dict, total_equity: float, current_positions: dict[str, float]) -> TradeValidation:
        # Check that post-trade single position wouldn't exceed max_position_pct
        # trade: {symbol, action, shares, price, target_weight}
        symbol = trade.get("symbol", "")
        target_w = float(trade.get("target_weight", 0))
        if target_w > self.max_position_pct + 1e-9:
            return TradeValidation(False, f"{symbol} target {target_w*100:.1f}% exceeds max {self.max_position_pct*100:.0f}%")
        return TradeValidation(True, "OK")

    def validate_batch(self, trades: list[dict], total_equity: float, current_positions: dict[str, float]) -> BatchValidationResult:
        # Turnover check
        total_turnover = 0.0
        if total_equity > 0:
            for t in trades:
                shares = float(t.get("shares", 0))
                price = float(t.get("price", 0))
                total_turnover += shares * price
            turnover_pct = total_turnover / total_equity if total_equity else 0
        else:
            turnover_pct = 0

        if turnover_pct > self.max_turnover_pct + 1e-9:
            return BatchValidationResult(
                valid=False,
                total_turnover_pct=turnover_pct,
                rejected_count=len(trades),
                reason=f"Turnover {turnover_pct*100:.1f}% exceeds limit {self.max_turnover_pct*100:.0f}%",
                details=[],
            )

        # Per-trade
        rejected = 0
        for t in trades:
            v = self.validate_trade(t, total_equity, current_positions)
            if not v.valid:
                rejected += 1
        if rejected:
            return BatchValidationResult(False, turnover_pct, rejected, f"{rejected} trade(s) exceed position limit {self.max_position_pct*100:.0f}%")

        return BatchValidationResult(True, turnover_pct, 0, "All checks passed")
