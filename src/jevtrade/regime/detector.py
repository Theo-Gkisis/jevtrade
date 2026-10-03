"""Market regime detection from indicators (rules only, no Jev)."""

from __future__ import annotations

from dataclasses import dataclass
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
    chaos_drop_pct: float = 5.0      # BTC drop from recent high that counts as a crash
    chaos_lookback_hours: int = 4    # window for measuring the drop
    chaos_cooldown_hours: int = 12   # stay in CHAOS this long after a crash
    squeeze_lookback_hours: int = 720  # 30 days of 1h candles
    squeeze_percentile: float = 10.0   # bb_width in the narrowest X% of the lookback = SQUEEZE


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



if __name__ == "__main__":
    from jevtrade.data.candles import create_exchange, fetch_candles
    from jevtrade.indicators.technical import add_bollinger 
    exchange = create_exchange()
    params = RegimeParams()
    btc_1h = fetch_candles(exchange, "BTC/USDT", "1h", limit=1000)

    drops = btc_drop_pct(btc_1h, params.chaos_lookback_hours)
    print(f"BTC drop from 4h high now: {drops.iloc[-1]:.2f}%")
    print(f"Max drop in last {params.chaos_cooldown_hours}h: {drops.tail(params.chaos_cooldown_hours).max():.2f}%")
    print(f"CHAOS now: {is_chaos(btc_1h, params)}")

    crash_hours = drops[drops > params.chaos_drop_pct]
    print(f"Hours with a crash signal in the last {len(btc_1h)} hours: {len(crash_hours)}")
    print(crash_hours.round(2).tail(10))




    print("--- SQUEEZE check (per coin) ---")
    for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        df_1h = add_bollinger(fetch_candles(exchange, symbol, "1h", limit=1000))
        rank = bb_width_percentile(df_1h, params.squeeze_lookback_hours)
        print(f"{symbol:<9} bb_width={df_1h['bb_width'].iloc[-1]:.2f}%  rank={rank:.0f}/100  SQUEEZE={is_squeeze(df_1h, params)}")

