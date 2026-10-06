"""Market orders on the Binance demo account: buy with a USDT amount, sell the whole coin balance."""

from __future__ import annotations

import ccxt


def market_buy(exchange: ccxt.binance, symbol: str, usdt_amount: float) -> dict:
    """Buy `symbol` now, spending `usdt_amount` USDT at the best available price."""
    return exchange.create_market_buy_order_with_cost(symbol, usdt_amount)


def market_sell_all(exchange: ccxt.binance, symbol: str) -> dict:
    """Sell the entire free balance of the coin (after fees we hold slightly less than we bought)."""
    base = symbol.split("/")[0]
    free = exchange.fetch_balance()["free"].get(base, 0.0)
    amount = float(exchange.amount_to_precision(symbol, free))
    return exchange.create_market_sell_order(symbol, amount)


def place_exit_oco(exchange: ccxt.binance, symbol: str, quantity: float, stop: float, target: float) -> dict:
    """Protect a long position at the exchange: sell at `target` (limit) OR at `stop` (market), whichever comes first.

    Binance POST /api/v3/orderList/oco with a LIMIT_MAKER above and a STOP_LOSS below.
    """
    return exchange.private_post_orderlist_oco(
        {
            "symbol": exchange.market(symbol)["id"],
            "side": "SELL",
            "quantity": exchange.amount_to_precision(symbol, quantity),
            "aboveType": "LIMIT_MAKER",
            "abovePrice": exchange.price_to_precision(symbol, target),
            "belowType": "STOP_LOSS",
            "belowStopPrice": exchange.price_to_precision(symbol, stop),
        }
    )


def cancel_oco(exchange: ccxt.binance, symbol: str, order_list_id: int) -> dict:
    """Cancel both legs of an OCO (e.g. to move the stop to breakeven, or to exit early)."""
    return exchange.private_delete_orderlist({"symbol": exchange.market(symbol)["id"], "orderListId": order_list_id})


def describe(order: dict) -> str:
    fees = ", ".join(f"{f['cost']:.8f} {f['currency']}" for f in order.get("fees") or [] if f.get("cost"))
    return (
        f"{order['side'].upper():<4} {order['symbol']}  filled={order['filled']:.8f}  "
        f"avg_price={order['average']:.2f}  cost={order['cost']:.4f} USDT  fees=[{fees}]  status={order['status']}"
    )


if __name__ == "__main__":
    from jevtrade.execution.demo import create_demo_exchange

    exchange = create_demo_exchange()
    exchange.load_markets()
    symbol = "BTC/USDT"

    buy = market_buy(exchange, symbol, 20)
    print(describe(buy))

    # Protect the position: stop 1% below, target 2% above the fill price
    quantity = exchange.fetch_balance()["free"]["BTC"]
    stop, target = buy["average"] * 0.99, buy["average"] * 1.02
    oco = place_exit_oco(exchange, symbol, quantity, stop, target)
    print(f"OCO  orderListId={oco['orderListId']}  stop={stop:.2f}  target={target:.2f}")

    for o in exchange.fetch_open_orders(symbol):
        print(f"  open: {o['info']['type']:<11} {o['side']}  amount={o['amount']}  price={o['price']}  stopPrice={o.get('triggerPrice')}")

    # Clean up the demo: cancel the OCO and sell everything back
    cancel_oco(exchange, symbol, oco["orderListId"])
    print("OCO cancelled")
    print(describe(market_sell_all(exchange, symbol)))
