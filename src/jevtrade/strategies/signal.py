"""A trade signal: what a strategy proposes. Strategies never size positions or place orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Signal:
    symbol: str
    strategy: str
    candle_time: datetime  # start time (UTC) of the 1h candle that triggered the signal
    entry: float
    stop: float
    target: float
    # Indicator values at signal time, kept for logging and later for Jev
    context: dict[str, float] = field(default_factory=dict)

    @property
    def risk_per_unit(self) -> float:
        """1R in price units: the distance from entry down to the stop."""
        return self.entry - self.stop

    @property
    def reward_risk(self) -> float:
        """How many R the target is away from the entry."""
        return (self.target - self.entry) / self.risk_per_unit
