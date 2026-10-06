"""Market regime detection from indicators (rules only, no Jev)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd


class Regime(StrEnum):
    CHAOS = "CHAOS"
    SQUEEZE = "SQUEEZE"
    TREND = "TREND"
    RANGE = "RANGE"
    NONE = "NONE"


@dataclass(frozen=True)
class RegimeParams:
    chaos_drop_pct: float = 5.0        # BTC drop from recent high that counts as a crash
    chaos_lookback_hours: int = 4      # window for measuring the drop
    chaos_cooldown_hours: int = 12     # stay in CHAOS this long after a crash
    squeeze_lookback_hours: int = 720  # 30 days of 1h candles
    squeeze_percentile: float = 10.0   # bb_width in the narrowest X% of the lookback = SQUEEZE
    trend_adx_min: float = 20.0        # ADX(4h) above this = a real trend
    range_adx_max: float = 20.0        # ADX(4h) below this = no clear direction
    range_lookback_hours: int = 48     # window for measuring the channel
    # Max channel height (high-low over the lookback, % of the low) per symbol
    range_max_width_pct: dict[str, float] = field(
        default_factory=lambda: {"BTC/USDT": 6.0, "ETH/USDT": 8.0, "SOL/USDT": 10.0}
    )


def btc_drop_pct(btc_1h: pd.DataFrame, lookback_hours: int) -> pd.Series:
    """For every 1h candle: % drop from the highest high of the last `lookback_hours` candles to the close."""
    recent_high = btc_1h["high"].rolling(lookback_hours).max()
    return (recent_high - btc_1h["close"]) / recent_high * 100


def is_chaos(btc_1h: pd.DataFrame, params: RegimeParams) -> bool:
    """True if BTC crashed (drop > chaos_drop_pct) at any point within the cooldown window."""
    drops = btc_drop_pct(btc_1h, params.chaos_lookback_hours)
    recent_drops = drops.tail(params.chaos_cooldown_hours)
    return bool((recent_drops > params.chaos_drop_pct).any())


def bb_width_percentile(df_1h: pd.DataFrame, lookback_hours: int) -> float:
    """Rank of the latest bb_width among the last `lookback_hours` widths (0 = narrowest, 100 = widest)."""
    window = df_1h["bb_width"].dropna().tail(lookback_hours)
    return float((window < window.iloc[-1]).mean() * 100)


def is_squeeze(df_1h: pd.DataFrame, params: RegimeParams) -> bool:
    """True if the latest 1h Bollinger width is among the narrowest of the lookback window."""
    return bb_width_percentile(df_1h, params.squeeze_lookback_hours) < params.squeeze_percentile


def is_trend(df_4h: pd.DataFrame, params: RegimeParams) -> bool:
    """True if the latest 4h close is above EMA50 and ADX shows a real trend."""
    last = df_4h.iloc[-1]
    return bool(last["close"] > last["ema_50"] and last["adx_14"] > params.trend_adx_min)


def channel_width_pct(df_1h: pd.DataFrame, lookback_hours: int) -> float:
    """Height of the price channel over the last `lookback_hours` 1h candles, as % of its low."""
    window = df_1h.tail(lookback_hours)
    low, high = window["low"].min(), window["high"].max()
    return float((high - low) / low * 100)


def is_range(df_4h: pd.DataFrame, df_1h: pd.DataFrame, symbol: str, params: RegimeParams) -> bool:
    """True if there is no clear trend (low ADX) and price stayed in a tight channel."""
    weak_trend = df_4h.iloc[-1]["adx_14"] < params.range_adx_max
    width = channel_width_pct(df_1h, params.range_lookback_hours)
    return bool(weak_trend and width < params.range_max_width_pct[symbol])


def detect_regime(
    symbol: str,
    btc_1h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    params: RegimeParams,
) -> Regime:
    """Return the single regime for `symbol`, checking in priority order.

    Expects df_1h with `bb_width` and df_4h with `ema_50` and `adx_14` already added.
    """
    if is_chaos(btc_1h, params):
        return Regime.CHAOS
    if is_squeeze(df_1h, params):
        return Regime.SQUEEZE
    if is_trend(df_4h, params):
        return Regime.TREND
    if is_range(df_4h, df_1h, symbol, params):
        return Regime.RANGE
    return Regime.NONE


if __name__ == "__main__":
    from jevtrade.data.candles import create_exchange, fetch_candles
    from jevtrade.indicators.technical import add_adx, add_bollinger, add_ema

    exchange = create_exchange()
    params = RegimeParams()
    btc_1h = fetch_candles(exchange, "BTC/USDT", "1h", limit=1000)
    print(f"Last closed 1h candle: {btc_1h.index[-1]:%Y-%m-%d %H:%M} UTC")

    for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        df_1h = add_bollinger(fetch_candles(exchange, symbol, "1h", limit=1000))
        df_4h = fetch_candles(exchange, symbol, "4h", limit=1000)
        add_ema(df_4h, 50)
        add_adx(df_4h, 14)

        regime = detect_regime(symbol, btc_1h, df_1h, df_4h, params)
        print(
            f"{symbol:<9} chaos={is_chaos(btc_1h, params)!s:<5}  squeeze={is_squeeze(df_1h, params)!s:<5}  "
            f"trend={is_trend(df_4h, params)!s:<5}  range={is_range(df_4h, df_1h, symbol, params)!s:<5}  "
            f"=> {regime}"
        )
