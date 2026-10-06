"""Strategy 3 - Breakout: after a squeeze, buy a close above the quiet period's high on strong volume."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from jevtrade.strategies.signal import Signal

STRATEGY_NAME = "breakout"


@dataclass(frozen=True)
class BreakoutParams:
    quiet_lookback: int = 24       # the quiet period = the 24 candles BEFORE the breakout candle
    volume_mult: float = 1.5       # breakout volume must be at least this x the average
    stop_inside_atr: float = 1.0   # stop this many ATRs back inside the quiet range
    target_r: float = 2.5


def quiet_range(df_1h: pd.DataFrame, lookback: int) -> tuple[float, float]:
    """Low and high of the quiet period, excluding the latest (breakout) candle."""
    window = df_1h.iloc[-lookback - 1 : -1]
    return float(window["low"].min()), float(window["high"].max())


def check_conditions(df_1h: pd.DataFrame, params: BreakoutParams) -> dict[str, bool]:
    """Evaluate the entry conditions on the latest closed 1h candle.

    Expects df_1h with `volume_ma_20`.
    """
    last = df_1h.iloc[-1]
    _, high = quiet_range(df_1h, params.quiet_lookback)
    return {
        "closed_above_range": bool(last["close"] > high),
        "volume_surge": bool(last["volume"] >= params.volume_mult * last["volume_ma_20"]),
        "green_candle": bool(last["close"] > last["open"]),
    }


def compute_levels(df_1h: pd.DataFrame, params: BreakoutParams) -> tuple[float, float, float]:
    """Entry at the latest close; if price falls back inside the range the breakout failed.

    The stop sits 1 ATR below the old ceiling, but never below the floor of the quiet range.
    Expects df_1h with `atr_14`.
    """
    last = df_1h.iloc[-1]
    low, high = quiet_range(df_1h, params.quiet_lookback)
    entry = float(last["close"])
    stop = max(low, high - params.stop_inside_atr * float(last["atr_14"]))
    target = entry + params.target_r * (entry - stop)
    return entry, stop, target


def find_signal(symbol: str, df_1h: pd.DataFrame, params: BreakoutParams) -> Signal | None:
    """Return a Signal if all conditions hold, otherwise None.

    Call this only when the regime is SQUEEZE.
    """
    if not all(check_conditions(df_1h, params).values()):
        return None

    entry, stop, target = compute_levels(df_1h, params)
    last = df_1h.iloc[-1]
    _, high = quiet_range(df_1h, params.quiet_lookback)
    return Signal(
        symbol=symbol,
        strategy=STRATEGY_NAME,
        candle_time=df_1h.index[-1].to_pydatetime(),
        entry=entry,
        stop=stop,
        target=target,
        context={
            "quiet_high": high,
            "volume_ratio": float(last["volume"] / last["volume_ma_20"]),
            "atr_14": float(last["atr_14"]),
        },
    )
