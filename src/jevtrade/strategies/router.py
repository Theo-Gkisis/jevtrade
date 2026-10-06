"""Send each coin to the one strategy that fits its regime."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from jevtrade.regime.detector import Regime
from jevtrade.strategies import breakout, range_reversion, trend_pullback
from jevtrade.strategies.signal import Signal


@dataclass(frozen=True)
class StrategyParams:
    trend_pullback: trend_pullback.TrendPullbackParams = field(default_factory=trend_pullback.TrendPullbackParams)
    range_reversion: range_reversion.RangeParams = field(default_factory=range_reversion.RangeParams)
    breakout: breakout.BreakoutParams = field(default_factory=breakout.BreakoutParams)


def check_conditions(
    regime: Regime, df_1h: pd.DataFrame, df_4h: pd.DataFrame, params: StrategyParams
) -> dict[str, bool]:
    """Conditions of the strategy active in this regime (empty for CHAOS / NONE)."""
    if regime == Regime.TREND:
        return trend_pullback.check_conditions(df_1h, df_4h, params.trend_pullback)
    if regime == Regime.RANGE:
        return range_reversion.check_conditions(df_1h, params.range_reversion)
    if regime == Regime.SQUEEZE:
        return breakout.check_conditions(df_1h, params.breakout)
    return {}


def find_signal(
    regime: Regime, symbol: str, df_1h: pd.DataFrame, df_4h: pd.DataFrame, params: StrategyParams
) -> Signal | None:
    """Run the strategy for this regime. CHAOS and NONE never produce a signal.

    Expects df_1h with ema_20, rsi_14, atr_14, Bollinger columns and volume_ma_20,
    and df_4h with ema_50.
    """
    if regime == Regime.TREND:
        return trend_pullback.find_signal(symbol, df_1h, df_4h, params.trend_pullback)
    if regime == Regime.RANGE:
        return range_reversion.find_signal(symbol, df_1h, params.range_reversion)
    if regime == Regime.SQUEEZE:
        return breakout.find_signal(symbol, df_1h, params.breakout)
    return None


if __name__ == "__main__":
    from jevtrade.data.candles import create_exchange, fetch_candles
    from jevtrade.indicators.technical import add_adx, add_atr, add_bollinger, add_ema, add_rsi, add_volume_ma
    from jevtrade.regime.detector import RegimeParams, detect_regime

    exchange = create_exchange()
    params = StrategyParams()
    regime_params = RegimeParams()
    btc_1h = fetch_candles(exchange, "BTC/USDT", "1h", limit=1000)
    print(f"Last closed 1h candle: {btc_1h.index[-1]:%Y-%m-%d %H:%M} UTC")

    for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        df_1h = fetch_candles(exchange, symbol, "1h", limit=1000)
        add_ema(df_1h, 20)
        add_rsi(df_1h, 14)
        add_atr(df_1h, 14)
        add_bollinger(df_1h)
        add_volume_ma(df_1h, 20)
        df_4h = fetch_candles(exchange, symbol, "4h", limit=1000)
        add_ema(df_4h, 50)
        add_adx(df_4h, 14)

        regime = detect_regime(symbol, btc_1h, df_1h, df_4h, regime_params)
        conditions = check_conditions(regime, df_1h, df_4h, params)
        signal = find_signal(regime, symbol, df_1h, df_4h, params)

        marks = "  ".join(f"{name}={'Y' if ok else '-'}" for name, ok in conditions.items()) or "(no strategy)"
        print(f"\n{symbol}  regime={regime}")
        print(f"  {marks}")
        if signal is None:
            print("  -> no signal")
        else:
            print(
                f"  -> SIGNAL [{signal.strategy}]  entry={signal.entry:.2f}  stop={signal.stop:.2f}  "
                f"target={signal.target:.2f}  1R={signal.risk_per_unit:.2f}  R:R={signal.reward_risk:.1f}"
            )
