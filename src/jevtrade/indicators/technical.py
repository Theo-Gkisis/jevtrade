"""Technical indicators computed locally with TA-Lib on our own candles."""

from __future__ import annotations

import pandas as pd
import talib

def add_ema(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Add column `ema_<period>`: exponential moving average of the close."""
    df[f"ema_{period}"] = talib.EMA(df["close"], timeperiod=period)
    return df

def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add column `rsi_<period>`: Relative Strength Index (0-100) of the close."""
    df[f"rsi_{period}"] = talib.RSI(df["close"], timeperiod=period)
    return df

def add_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Add column `volume_ma_<period>`: simple average volume of the last `period` candles."""
    df[f"volume_ma_{period}"] = talib.SMA(df["volume"], timeperiod=period)
    return df

def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add column `adx_<period>`: trend strength (0-100). Strength only, not direction."""
    df[f"adx_{period}"] = talib.ADX(df["high"], df["low"], df["close"], timeperiod=period)
    return df

def add_bollinger(df: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Add bb_upper / bb_middle / bb_lower and bb_width (band width as % of the middle)."""
    upper, middle, lower = talib.BBANDS(
        df["close"], timeperiod=period, nbdevup=num_std, nbdevdn=num_std
    )
    df["bb_upper"] = upper
    df["bb_middle"] = middle
    df["bb_lower"] = lower
    df["bb_width"] = (upper - lower) / middle * 100
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add column `atr_<period>`: Average True Range, the typical candle move in price units."""
    df[f"atr_{period}"] = talib.ATR(df["high"], df["low"], df["close"], timeperiod=period)
    return df

if __name__ == "__main__":
    from jevtrade.data.candles import create_exchange, fetch_candles

    exchange = create_exchange()

    # Question 1: is the market going up? (4h close vs EMA50)
    df_4h = add_ema(fetch_candles(exchange, "BTC/USDT", "4h", limit=1000), 50)
    add_adx(df_4h, 14)
    last_4h = df_4h.iloc[-1]
    uptrend = last_4h["close"] > last_4h["ema_50"]
    print(f"4h  close={last_4h['close']:.2f}  ema_50={last_4h['ema_50']:.2f}  uptrend={uptrend}")

    # Question 2: how far is price from EMA20, and how "hot" is the move? (1h)
    df_1h = fetch_candles(exchange, "BTC/USDT", "1h", limit=1000)
    add_ema(df_1h, 20)
    add_rsi(df_1h, 14)
    add_atr(df_1h, 14)
    add_bollinger(df_1h)
    add_volume_ma(df_1h, 20)
    last_1h = df_1h.iloc[-1]
    distance_pct = (last_1h["close"] - last_1h["ema_20"]) / last_1h["ema_20"] * 100
    print(f"1h  close={last_1h['close']:.2f}  ema_20={last_1h['ema_20']:.2f}  distance={distance_pct:+.2f}%")
    print(f"1h  rsi_14={last_1h['rsi_14']:.1f}  pullback_zone={40 <= last_1h['rsi_14'] <= 55}")
    atr = last_1h["atr_14"]
    atr_pct = atr / last_1h["close"] * 100
    stop_price = last_1h["close"] - 1.5 * atr
    print(f"1h  atr_14={atr:.2f} ({atr_pct:.2f}% of price)  stop_if_buy_now={stop_price:.2f}")
    adx = last_4h["adx_14"]
    regime_hint = "TREND" if uptrend and adx > 20 else "NOT TREND"
    print(f"4h  adx_14={adx:.1f}  strong_trend={adx > 20}  regime_hint={regime_hint}")
    bb_position = (last_1h["close"] - last_1h["bb_lower"]) / (last_1h["bb_upper"] - last_1h["bb_lower"])
    print(
        f"1h  bb_lower={last_1h['bb_lower']:.2f}  bb_middle={last_1h['bb_middle']:.2f}  "
        f"bb_upper={last_1h['bb_upper']:.2f}  bb_width={last_1h['bb_width']:.2f}%  position={bb_position:.2f}"
    )
    volume_ratio = last_1h["volume"] / last_1h["volume_ma_20"]
    print(
        f"1h  volume={last_1h['volume']:.1f}  volume_ma_20={last_1h['volume_ma_20']:.1f}  "
        f"ratio={volume_ratio:.2f}x  above_avg={volume_ratio > 1}  breakout_volume={volume_ratio >= 1.5}"
    )