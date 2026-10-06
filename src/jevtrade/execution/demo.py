"""Binance Demo Trading connection (demo.binance.com). The keys used here can never reach the real exchange."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import ccxt
from dotenv import load_dotenv

DEMO_HOST = "demo-api.binance.com"


def create_demo_exchange() -> ccxt.binance:
    """Authenticated Binance client locked to Demo Trading (virtual money, real market prices).

    Reads BINANCE_DEMO_API_KEY and BINANCE_DEMO_API_SECRET from the environment or .env.
    """
    load_dotenv()
    api_key = os.environ.get("BINANCE_DEMO_API_KEY")
    secret = os.environ.get("BINANCE_DEMO_API_SECRET")
    if not api_key or not secret:
        raise RuntimeError("BINANCE_DEMO_API_KEY / BINANCE_DEMO_API_SECRET missing (check .env)")

    exchange = ccxt.binance(
        {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            # Signed requests are rejected if our clock drifts from Binance's; sync to server time
            "options": {"adjustForTimeDifference": True},
        }
    )
    exchange.enable_demo_trading(True)

    # Guard: refuse to continue unless authenticated calls go to Demo Trading
    host = urlparse(exchange.urls["api"]["private"]).hostname
    if host != DEMO_HOST:
        raise RuntimeError(f"refusing to trade: private API points to {host}, expected {DEMO_HOST}")
    return exchange


if __name__ == "__main__":
    exchange = create_demo_exchange()
    print(f"Connected to {exchange.urls['api']['private']}")

    balance = exchange.fetch_balance()
    for asset in ["USDT", "BTC", "ETH", "SOL"]:
        print(f"{asset:<5} free={balance['free'].get(asset, 0):>14.4f}  total={balance['total'].get(asset, 0):>14.4f}")
