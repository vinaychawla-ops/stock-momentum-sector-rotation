"""Broker abstraction — PaperBroker implements this; future LiveBroker can too."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class ExecutionResult:
    order_id: str
    symbol: str
    action: str
    shares: int
    order_type: str
    status: str  # submitted | filled | dry_run | rejected
    fill_price: Optional[float] = None
    fill_time: Optional[str] = None
    message: str = ""
    dry_run: bool = False


class Broker(Protocol):
    def submit_batch(self, trades: list[dict]) -> list[ExecutionResult]: ...
    def connect(self) -> bool: ...
    def disconnect(self) -> None: ...
