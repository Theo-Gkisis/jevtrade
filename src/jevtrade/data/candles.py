from __future__ import annotations

import ccxt
import pandas as pd

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

def create_exchange() -> ccxt.binance:
    """Binance client for public data. Without an API key it cannot place orders."""
    return ccxt.binance({"enableRateLimit": True})

def fetch_candles(exchange: ccxt.Exchange,symbol: str,timeframe: str = "1h",limit: int = 100) -> pd.DataFrame:
    
    """Return the last `limit` CLOSED candles for `symbol`, oldest first."""
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit + 1)
    df = pd.DataFrame(raw, columns=OHLCV_COLUMNS)

    # The newest candle is usually still forming; decisions use closed candles only.
    candle_ms = exchange.parse_timeframe(timeframe) * 1000
    now_ms = exchange.milliseconds()
    df = df[df["timestamp"] + candle_ms <= now_ms]

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.tail(limit).set_index("timestamp")

if __name__ == "__main__":
    exchange = create_exchange()
    candles = fetch_candles(exchange, "BTC/USDT", "1h", limit=10)
    print(candles)