"""The hourly cycle: manage open positions, then look for new signals and trade them on Binance Demo Trading.

Run once:          python -m jevtrade.run
Run every hour:    python -m jevtrade.run --loop
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import ccxt
import pandas as pd
from sqlalchemy.orm import Session

from jevtrade.data.candles import create_exchange, fetch_candles
from jevtrade.execution.demo import create_demo_exchange
from jevtrade.execution.orders import cancel_oco, market_buy, place_exit_oco
from jevtrade.indicators.technical import add_adx, add_atr, add_bollinger, add_ema, add_rsi, add_volume_ma
from jevtrade.jev.client import JevAssessment, JevClient, build_state
from jevtrade.jev.policy import JevDecision, JevPolicyParams, decide
from jevtrade.regime.detector import RegimeParams, detect_regime
from jevtrade.risk.limits import LimitParams, check_limits
from jevtrade.risk.sizing import SizingParams, position_size
from jevtrade.storage.db import create_db_engine
from jevtrade.storage.models import PositionRow, create_tables
from jevtrade.storage.repository import (
    add_position,
    close_position,
    open_positions,
    portfolio_state,
    record_jev_decision,
    record_signal,
    signal_exists,
)
from jevtrade.strategies.signal import Signal
from jevtrade.strategies.router import StrategyParams, find_signal

log = logging.getLogger("jevtrade")


@dataclass(frozen=True)
class BotParams:
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
    time_stop_hours: int = 48       # exit if neither stop nor target was hit after this long
    breakeven_at_r: float = 1.0     # move the stop to entry once the price has gone this many R up
    fee_pct: float = 0.1            # used to estimate exit fees (Binance spot taker fee)
    jev_mode: str = "enforce"       # "enforce": Jev can veto or shrink trades; "off": rules only
    regime: RegimeParams = field(default_factory=RegimeParams)
    jev_policy: JevPolicyParams = field(default_factory=JevPolicyParams)
    strategies: StrategyParams = field(default_factory=StrategyParams)
    sizing: SizingParams = field(default_factory=SizingParams)
    limits: LimitParams = field(default_factory=LimitParams)


# --- market data -------------------------------------------------------------------------------


def load_market_data(market: ccxt.binance, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Closed 1h and 4h candles with every indicator the regime and strategies read."""
    df_1h = fetch_candles(market, symbol, "1h", limit=1000)
    add_ema(df_1h, 20)
    add_rsi(df_1h, 14)
    add_atr(df_1h, 14)
    add_bollinger(df_1h)
    add_volume_ma(df_1h, 20)
    df_4h = fetch_candles(market, symbol, "4h", limit=1000)
    add_ema(df_4h, 50)
    add_adx(df_4h, 14)
    return df_1h, df_4h


def account_equity(exchange: ccxt.binance, prices: dict[str, float]) -> float:
    """USDT plus the current value of the coins we trade."""
    total = exchange.fetch_balance()["total"]
    return total.get("USDT", 0.0) + sum(total.get(s.split("/")[0], 0.0) * p for s, p in prices.items())


# --- order helpers -----------------------------------------------------------------------------


def fees_of(order: dict, symbol: str, price: float) -> tuple[float, float]:
    """(fee in USDT, fee paid in the coin itself) for a filled order."""
    base = symbol.split("/")[0]
    usdt, in_base = 0.0, 0.0
    for fee in order.get("fees") or []:
        if not fee.get("cost"):
            continue
        if fee["currency"] == "USDT":
            usdt += fee["cost"]
        elif fee["currency"] == base:
            in_base += fee["cost"]
            usdt += fee["cost"] * price
    return usdt, in_base


def exit_at_market(session: Session, exchange: ccxt.binance, position: PositionRow, now: datetime, reason: str, fee_pct: float) -> None:
    """Cancel the protective OCO (if any), sell the position at market and record the trade."""
    if position.oco_order_list_id:
        cancel_oco(exchange, position.symbol, position.oco_order_list_id)
    amount = float(exchange.amount_to_precision(position.symbol, position.quantity))
    order = exchange.create_market_sell_order(position.symbol, amount)
    fee_usdt, _ = fees_of(order, position.symbol, order["average"])
    fee_usdt = fee_usdt or order["cost"] * fee_pct / 100
    trade = close_position(session, position, order["average"], fee_usdt, now, reason)
    log.info("EXIT %s reason=%s pnl=%+.2f USDT R=%+.2f", trade.symbol, reason, trade.pnl_usdt, trade.r_multiple)


def sync_with_exchange(session: Session, exchange: ccxt.binance, position: PositionRow, now: datetime, fee_pct: float) -> bool:
    """If Binance already closed the position through the OCO, record the trade. Returns True if it was closed."""
    if not position.oco_order_list_id:
        return False
    order_list = exchange.private_get_orderlist({"orderListId": position.oco_order_list_id})
    if order_list["listOrderStatus"] != "ALL_DONE":
        return False

    for leg in order_list["orders"]:
        order = exchange.fetch_order(str(leg["orderId"]), position.symbol)
        if order["status"] == "closed" and order["filled"]:
            if order["info"]["type"] == "LIMIT_MAKER":
                reason = "target"
            else:
                reason = "breakeven" if position.stop >= position.entry_price else "stop"
            trade = close_position(session, position, order["average"], order["cost"] * fee_pct / 100, now, reason)
            log.info("EXIT %s reason=%s pnl=%+.2f USDT R=%+.2f (filled by OCO)", trade.symbol, reason, trade.pnl_usdt, trade.r_multiple)
            return True

    log.warning("OCO %s for %s is done but no leg filled (cancelled outside the bot?)", position.oco_order_list_id, position.symbol)
    return False


# --- Jev ---------------------------------------------------------------------------------------


def ask_jev(
    jev: JevClient | None, signal: Signal, regime: str, df_1h: pd.DataFrame, df_4h: pd.DataFrame, params: BotParams
) -> tuple[dict, JevAssessment | None, JevDecision, str | None]:
    """Ask Jev about a signal and apply our policy. Any failure means reject (fail-closed).

    Returns (state sent, Jev's answer or None, our decision, error text or None).
    """
    state = build_state(signal, df_1h, df_4h, max_holding_hours=params.time_stop_hours)
    is_breakout = signal.strategy == "breakout"
    assessment, error = None, None
    if jev is None:
        error = "Jev client not configured"
    else:
        try:
            assessment = jev.assess(state, is_breakout=is_breakout)
        except Exception as exc:  # network, HTTP error, unexpected answer format: all fail closed
            error = f"{type(exc).__name__}: {exc}"
    decision = decide(assessment, regime, is_breakout, params.jev_policy)
    if assessment:
        log.info(
            "JEV %s regime=%s (%.2f) target_p=%.2f news=%.2f -> %s x%.1f %s",
            signal.symbol, assessment.regime, assessment.regime_confidence, assessment.target_probability,
            assessment.news_risk, decision.action, decision.risk_multiplier, "; ".join(decision.reasons),
        )
    else:
        log.warning("JEV %s unavailable (%s) -> reject", signal.symbol, error)
    return state, assessment, decision, error


# --- the two halves of the hourly cycle ----------------------------------------------------------


def manage_positions(
    session: Session,
    exchange: ccxt.binance,
    data: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    prices: dict[str, float],
    params: BotParams,
    now: datetime,
) -> None:
    """Exits: OCO fills, monthly loss limit, time stop, and moving the stop to breakeven."""
    for position in open_positions(session):
        sync_with_exchange(session, exchange, position, now, params.fee_pct)

    state = portfolio_state(session, account_equity(exchange, prices), prices, now)
    if state.month_pnl_pct <= -params.limits.monthly_loss_limit_pct:
        log.warning("monthly loss %.2f%% hit the limit: closing everything", state.month_pnl_pct)
        for position in open_positions(session):
            exit_at_market(session, exchange, position, now, "monthly_limit", params.fee_pct)
        return

    for position in open_positions(session):
        symbol = position.symbol
        if now - position.opened_at >= timedelta(hours=params.time_stop_hours):
            exit_at_market(session, exchange, position, now, "time", params.fee_pct)
            continue

        r_unit = position.entry_price - position.initial_stop
        last_high = float(data[symbol][0].iloc[-1]["high"])
        reached_1r = last_high >= position.entry_price + params.breakeven_at_r * r_unit
        if position.stop < position.entry_price and reached_1r:
            if prices[symbol] <= position.entry_price:
                # Already back at entry: a stop at entry would trigger instantly, so just exit
                exit_at_market(session, exchange, position, now, "breakeven", params.fee_pct)
                continue
            cancel_oco(exchange, symbol, position.oco_order_list_id)
            oco = place_exit_oco(exchange, symbol, position.quantity, position.entry_price, position.target)
            position.stop = position.entry_price
            position.oco_order_list_id = int(oco["orderListId"])
            log.info("BREAKEVEN %s stop moved to %.2f", symbol, position.stop)


def look_for_signals(
    session: Session,
    exchange: ccxt.binance,
    data: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    prices: dict[str, float],
    params: BotParams,
    now: datetime,
) -> None:
    """Entries: regime -> strategy -> Jev -> risk limits -> market buy + protective OCO."""
    btc_1h = data["BTC/USDT"][0]
    jev = None
    if params.jev_mode == "enforce":
        try:
            jev = JevClient()
        except RuntimeError as exc:
            log.error("Jev client unavailable (%s): every signal will be rejected (fail-closed)", exc)

    for symbol in params.symbols:
        df_1h, df_4h = data[symbol]
        regime = detect_regime(symbol, btc_1h, df_1h, df_4h, params.regime)
        signal = find_signal(regime, symbol, df_1h, df_4h, params.strategies)
        log.info("%s regime=%s signal=%s", symbol, regime, signal.strategy if signal else "none")
        if signal is None or signal_exists(session, signal):
            continue

        # Second opinion: Jev may veto the trade or shrink its risk, never enlarge it
        jev_review = None
        if params.jev_mode == "enforce":
            jev_review = ask_jev(jev, signal, regime, df_1h, df_4h, params)
            state, assessment, decision, error = jev_review
            if decision.action == "reject":
                signal_row = record_signal(session, signal, regime, "jev_reject", "; ".join(decision.reasons))
                record_jev_decision(session, signal_row, state, assessment, decision, error)
                session.commit()
                continue
        risk_multiplier = jev_review[2].risk_multiplier if jev_review else 1.0

        equity = account_equity(exchange, prices)
        size = position_size(signal, equity, params.sizing, risk_pct=params.sizing.risk_per_trade_pct * risk_multiplier)
        reasons = check_limits(symbol, size.risk_pct, portfolio_state(session, equity, prices, now), params.limits)
        cost = size.quantity * signal.entry
        free_usdt = exchange.fetch_balance()["free"].get("USDT", 0.0)
        min_cost = exchange.market(symbol)["limits"]["cost"]["min"] or 0.0
        if cost > free_usdt * 0.99:
            reasons.append(f"not enough free USDT ({free_usdt:.2f} for {cost:.2f})")
        if cost < min_cost:
            reasons.append(f"order of {cost:.2f} USDT is below the exchange minimum {min_cost}")
        if reasons:
            signal_row = record_signal(session, signal, regime, "blocked", "; ".join(reasons))
            if jev_review:
                record_jev_decision(session, signal_row, jev_review[0], jev_review[1], jev_review[2], jev_review[3])
            session.commit()
            log.info("BLOCKED %s: %s", symbol, "; ".join(reasons))
            continue

        buy = market_buy(exchange, symbol, cost)
        fill = float(buy["average"])
        fee_usdt, fee_in_coin = fees_of(buy, symbol, fill)
        quantity = float(exchange.amount_to_precision(symbol, buy["filled"] - fee_in_coin))
        signal_row = record_signal(session, signal, regime, "opened")
        if jev_review:
            record_jev_decision(session, signal_row, jev_review[0], jev_review[1], jev_review[2], jev_review[3])
        position = add_position(session, signal_row, now, fill, quantity, fee_usdt, str(buy["id"]), None)
        log.info("BUY %s qty=%s @ %.2f (cost %.2f USDT) stop=%.2f target=%.2f", symbol, quantity, fill, buy["cost"], signal.stop, signal.target)

        try:
            oco = place_exit_oco(exchange, symbol, quantity, signal.stop, signal.target)
            position.oco_order_list_id = int(oco["orderListId"])
        except ccxt.BaseError as exc:
            # Never keep an unprotected position: sell it straight away
            log.error("could not place the OCO for %s (%s): exiting at market", symbol, exc)
            exit_at_market(session, exchange, position, now, "protection_failed", params.fee_pct)
        session.commit()


def run_once(params: BotParams) -> None:
    now = datetime.now(UTC)
    exchange = create_demo_exchange()
    exchange.load_markets()
    market = create_exchange()
    engine = create_db_engine()
    create_tables(engine)

    data = {symbol: load_market_data(market, symbol) for symbol in params.symbols}
    prices = {symbol: float(exchange.fetch_ticker(symbol)["last"]) for symbol in params.symbols}
    log.info("cycle at %s, last closed candle %s", now.strftime("%Y-%m-%d %H:%M UTC"), data["BTC/USDT"][0].index[-1])

    with Session(engine) as session:
        manage_positions(session, exchange, data, prices, params, now)
        session.commit()
        look_for_signals(session, exchange, data, prices, params, now)
        session.commit()

    log.info("equity %.2f USDT", account_equity(exchange, prices))


def seconds_until_next_hour(delay_seconds: int = 60) -> float:
    """Wait until just after the next 1h candle closes (Binance needs a moment to publish it)."""
    now = datetime.now(UTC)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (next_hour - now).total_seconds() + delay_seconds


def main() -> None:
    parser = argparse.ArgumentParser(description="jevtrade hourly cycle on Binance Demo Trading")
    parser.add_argument("--loop", action="store_true", help="keep running, once per hour")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per HTTP request is noise
    params = BotParams()

    while True:
        try:
            run_once(params)
        except Exception:
            # One bad cycle (network, exchange error) must not kill the loop; OCOs keep protecting positions
            log.exception("cycle failed")
            if not args.loop:
                raise
        if not args.loop:
            break
        wait = seconds_until_next_hour()
        log.info("sleeping %.0f minutes", wait / 60)
        time.sleep(wait)


if __name__ == "__main__":
    main()
