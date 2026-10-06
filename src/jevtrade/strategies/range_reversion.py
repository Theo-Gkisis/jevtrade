"""Strategy 2 - Range: buy near the floor of a sideways channel, target the middle of the channel."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from jevtrade.strategies.signal import Signal

STRATEGY_NAME = "range"


@dataclass(frozen=True)
class RangeParams:
    channel_lookback: int = 48         # same window the RANGE regime uses
    floor_tolerance_pct: float = 0.5   # close within this % above the channel low = "near the floor"
    rsi_max: float = 35.0
    stop_buffer_atr: float = 0.5       # stop this many ATRs below the channel low
    min_reward_r: float = 1.0          # skip setups where the middle is too close to be worth the risk


def channel_levels(df_1h: pd.DataFrame, lookback: int) -> tuple[float, float]:
    """Lowest low and highest high of the last `lookback` 1h candles."""
    window = df_1h.tail(lookback)
    return float(window["low"].min()), float(window["high"].max())


def check_conditions(df_1h: pd.DataFrame, params: RangeParams) -> dict[str, bool]:
    """Evaluate the entry conditions on the latest closed 1h candle.

    Expects df_1h with `rsi_14` and `bb_lower`.
    """
    last = df_1h.iloc[-1]
    low, _ = channel_levels(df_1h, params.channel_lookback)
    near_channel_low = last["close"] <= low * (1 + params.floor_tolerance_pct / 100)
    at_lower_band = last["close"] <= last["bb_lower"]
    return {
        "near_floor": bool(near_channel_low or at_lower_band),
        "rsi_oversold": bool(last["rsi_14"] < params.rsi_max),
    }


def compute_levels(df_1h: pd.DataFrame, params: RangeParams) -> tuple[float, float, float]:
    """Entry at the latest close, stop just below the floor, target at the middle of the channel.

    Expects df_1h with `atr_14`.
    """
    last = df_1h.iloc[-1]
    low, high = channel_levels(df_1h, params.channel_lookback)
    entry = float(last["close"])
    stop = low - params.stop_buffer_atr * float(last["atr_14"])
    target = (low + high) / 2
    return entry, stop, target


def find_signal(symbol: str, df_1h: pd.DataFrame, params: RangeParams) -> Signal | None:
    """Return a Signal if the conditions hold and the reward is worth it, otherwise None.

    Call this only when the regime is RANGE.
    """
    if not all(check_conditions(df_1h, params).values()):
        return None

    entry, stop, target = compute_levels(df_1h, params)
    if target <= entry or (target - entry) / (entry - stop) < params.min_reward_r:
        return None

    last = df_1h.iloc[-1]
    return Signal(
        symbol=symbol,
        strategy=STRATEGY_NAME,
        candle_time=df_1h.index[-1].to_pydatetime(),
        entry=entry,
        stop=stop,
        target=target,
        context={
            "rsi_14": float(last["rsi_14"]),
            "bb_lower": float(last["bb_lower"]),
            "atr_14": float(last["atr_14"]),
        },
    )
