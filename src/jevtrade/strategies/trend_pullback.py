"""Strategy 1 - Trend Pullback: buy a mild dip to EMA20 inside a 4h uptrend."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from jevtrade.strategies.signal import Signal

STRATEGY_NAME = "trend_pullback"


@dataclass(frozen=True)
class TrendPullbackParams:
    touch_lookback: int = 4            # the EMA20 touch must happen within the last N 1h candles
    touch_tolerance_pct: float = 0.3   # a low within this % above EMA20 counts as a touch
    rsi_min: float = 40.0
    rsi_max: float = 55.0
    swing_low_lookback: int = 5        # candles used to find the low of the pullback
    atr_stop_mult: float = 1.5         # minimum stop distance in ATRs
    target_r: float = 2.0              # take profit distance in R


def check_conditions(df_1h: pd.DataFrame, df_4h: pd.DataFrame, params: TrendPullbackParams) -> dict[str, bool]:
    """Evaluate the 4 entry conditions on the latest closed candles.

    Expects df_1h with `ema_20` and `rsi_14`, and df_4h with `ema_50`.
    """
    last_4h = df_4h.iloc[-1]
    last_1h = df_1h.iloc[-1]
    recent = df_1h.tail(params.touch_lookback)
    touch_level = recent["ema_20"] * (1 + params.touch_tolerance_pct / 100)

    return {
        "above_ema50_4h": bool(last_4h["close"] > last_4h["ema_50"]),
        "touched_ema20": bool((recent["low"] <= touch_level).any()),
        "rsi_in_zone": bool(params.rsi_min <= last_1h["rsi_14"] <= params.rsi_max),
        "green_candle": bool(last_1h["close"] > last_1h["open"]),
    }


def compute_levels(df_1h: pd.DataFrame, params: TrendPullbackParams) -> tuple[float, float, float]:
    """Entry, stop and target if we bought at the latest close.

    The stop is the LOWER of the pullback low and entry - 1.5 x ATR, so it is never
    closer than 1.5 ATR (normal noise) and never above the low of the dip.
    Expects df_1h with `atr_14`.
    """
    last = df_1h.iloc[-1]
    entry = float(last["close"])
    swing_low = float(df_1h["low"].tail(params.swing_low_lookback).min())
    atr_stop = entry - params.atr_stop_mult * float(last["atr_14"])
    stop = min(swing_low, atr_stop)
    target = entry + params.target_r * (entry - stop)
    return entry, stop, target


def find_signal(
    symbol: str, df_1h: pd.DataFrame, df_4h: pd.DataFrame, params: TrendPullbackParams
) -> Signal | None:
    """Return a Signal if all 4 conditions hold, otherwise None.

    Call this only when the regime is TREND. Expects df_1h with `ema_20`, `rsi_14`,
    `atr_14`, and df_4h with `ema_50`.
    """
    if not all(check_conditions(df_1h, df_4h, params).values()):
        return None

    entry, stop, target = compute_levels(df_1h, params)
    last_1h = df_1h.iloc[-1]
    return Signal(
        symbol=symbol,
        strategy=STRATEGY_NAME,
        candle_time=df_1h.index[-1].to_pydatetime(),
        entry=entry,
        stop=stop,
        target=target,
        context={
            "ema_20": float(last_1h["ema_20"]),
            "rsi_14": float(last_1h["rsi_14"]),
            "atr_14": float(last_1h["atr_14"]),
            "ema_50_4h": float(df_4h.iloc[-1]["ema_50"]),
        },
    )


if __name__ == "__main__":
    from jevtrade.data.candles import create_exchange, fetch_candles
    from jevtrade.indicators.technical import add_adx, add_atr, add_bollinger, add_ema, add_rsi
    from jevtrade.regime.detector import Regime, RegimeParams, detect_regime

    exchange = create_exchange()
    params = TrendPullbackParams()
    regime_params = RegimeParams()
    btc_1h = fetch_candles(exchange, "BTC/USDT", "1h", limit=1000)
    print(f"Last closed 1h candle: {btc_1h.index[-1]:%Y-%m-%d %H:%M} UTC")

    for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        df_1h = fetch_candles(exchange, symbol, "1h", limit=1000)
        add_ema(df_1h, 20)
        add_rsi(df_1h, 14)
        add_atr(df_1h, 14)
        add_bollinger(df_1h)
        df_4h = fetch_candles(exchange, symbol, "4h", limit=1000)
        add_ema(df_4h, 50)
        add_adx(df_4h, 14)

        regime = detect_regime(symbol, btc_1h, df_1h, df_4h, regime_params)
        conditions = check_conditions(df_1h, df_4h, params)
        last = df_1h.iloc[-1]
        marks = "  ".join(f"{name}={'Y' if ok else '-'}" for name, ok in conditions.items())

        print(f"\n{symbol}  regime={regime}")
        print(f"  {marks}")
        print(f"  close={last['close']:.2f}  ema_20={last['ema_20']:.2f}  rsi_14={last['rsi_14']:.1f}  atr_14={last['atr_14']:.2f}")

        if regime != Regime.TREND:
            print("  -> strategy not active (regime is not TREND)")
            continue

        signal = find_signal(symbol, df_1h, df_4h, params)
        if signal is None:
            entry, stop, target = compute_levels(df_1h, params)
            print(f"  -> no signal  (if it fired now: entry={entry:.2f}  stop={stop:.2f}  target={target:.2f})")
        else:
            print(
                f"  -> SIGNAL  entry={signal.entry:.2f}  stop={signal.stop:.2f}  target={signal.target:.2f}  "
                f"1R={signal.risk_per_unit:.2f}  R:R={signal.reward_risk:.1f}"
            )
