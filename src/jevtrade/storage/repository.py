"""Read and write the bot's journal: signals, open positions and closed trades."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jevtrade.jev.client import JevAssessment
from jevtrade.jev.policy import JevDecision
from jevtrade.risk.limits import PortfolioState
from jevtrade.storage.models import CycleRow, JevDecisionRow, PositionRow, SignalRow, TradeRow
from jevtrade.strategies.signal import Signal


def signal_exists(session: Session, signal: Signal) -> bool:
    """True if this strategy already produced a signal for this coin on this candle (e.g. the bot ran twice)."""
    query = select(SignalRow.id).where(
        SignalRow.symbol == signal.symbol,
        SignalRow.strategy == signal.strategy,
        SignalRow.candle_time == signal.candle_time,
    )
    return session.scalar(query) is not None


def record_signal(
    session: Session, signal: Signal, regime: str, status: str, block_reason: str | None = None
) -> SignalRow:
    """Store a signal. `status` is "opened" or "blocked"."""
    row = SignalRow(
        candle_time=signal.candle_time,
        symbol=signal.symbol,
        strategy=signal.strategy,
        regime=regime,
        entry=signal.entry,
        stop=signal.stop,
        target=signal.target,
        context=signal.context,
        status=status,
        block_reason=block_reason,
    )
    session.add(row)
    session.flush()  # assigns row.id
    return row


def record_jev_decision(
    session: Session,
    signal_row: SignalRow,
    state: dict,
    assessment: JevAssessment | None,
    decision: JevDecision,
    error: str | None = None,
) -> JevDecisionRow:
    """Store what we asked Jev, what it answered (or why it failed) and what our policy decided."""
    a = assessment
    row = JevDecisionRow(
        signal_id=signal_row.id,
        model=a.raw.get("model") if a else None,
        state=state,
        response=a.raw if a else None,
        jev_regime=a.regime if a else None,
        regime_confidence=a.regime_confidence if a else None,
        target_probability=a.target_probability if a else None,
        news_risk=a.news_risk if a else None,
        breakout_genuine=a.breakout_genuine if a else None,
        action=decision.action,
        risk_multiplier=decision.risk_multiplier,
        reasons="; ".join(decision.reasons) or None,
        latency_ms=a.latency_ms if a else None,
        cost_usd=a.cost_usd if a else None,
        error=error[:500] if error else None,
    )
    session.add(row)
    session.flush()
    return row


def open_positions(session: Session) -> list[PositionRow]:
    return list(session.scalars(select(PositionRow)))


def add_position(
    session: Session,
    signal_row: SignalRow,
    opened_at: datetime,
    entry_price: float,
    quantity: float,
    entry_fee_usdt: float,
    entry_order_id: str,
    oco_order_list_id: int | None,
) -> PositionRow:
    row = PositionRow(
        signal_id=signal_row.id,
        symbol=signal_row.symbol,
        strategy=signal_row.strategy,
        opened_at=opened_at,
        entry_price=entry_price,
        quantity=quantity,
        entry_fee_usdt=entry_fee_usdt,
        initial_stop=signal_row.stop,
        stop=signal_row.stop,
        target=signal_row.target,
        entry_order_id=entry_order_id,
        oco_order_list_id=oco_order_list_id,
    )
    session.add(row)
    session.flush()
    return row


def close_position(
    session: Session, position: PositionRow, exit_price: float, exit_fee_usdt: float, closed_at: datetime, reason: str
) -> TradeRow:
    """Turn an open position into a trade: compute the net result in USDT and in R, then delete the position."""
    pnl = position.quantity * (exit_price - position.entry_price) - position.entry_fee_usdt - exit_fee_usdt
    risk = position.quantity * (position.entry_price - position.initial_stop)
    trade = TradeRow(
        signal_id=position.signal_id,
        symbol=position.symbol,
        strategy=position.strategy,
        opened_at=position.opened_at,
        closed_at=closed_at,
        entry_price=position.entry_price,
        exit_price=exit_price,
        quantity=position.quantity,
        fees_usdt=position.entry_fee_usdt + exit_fee_usdt,
        pnl_usdt=pnl,
        r_multiple=pnl / risk if risk > 0 else 0.0,
        exit_reason=reason,
    )
    session.add(trade)
    session.delete(position)
    session.flush()
    return trade


def record_cycle(
    session: Session,
    ran_at: datetime,
    duration_ms: int,
    equity_usdt: float | None = None,
    open_positions_count: int | None = None,
    regimes: dict[str, str] | None = None,
    signals_found: int | None = None,
    error: str | None = None,
) -> CycleRow:
    """Store one hourly cycle (a failed cycle has only ran_at, duration and error)."""
    row = CycleRow(
        ran_at=ran_at,
        equity_usdt=equity_usdt,
        open_positions=open_positions_count,
        regimes=regimes,
        signals_found=signals_found,
        duration_ms=duration_ms,
        error=error[:1000] if error else None,
    )
    session.add(row)
    session.flush()
    return row


def realized_pnl_since(session: Session, since: datetime) -> float:
    query = select(func.coalesce(func.sum(TradeRow.pnl_usdt), 0.0)).where(TradeRow.closed_at >= since)
    return float(session.scalar(query))


def portfolio_state(session: Session, equity: float, prices: dict[str, float], now: datetime) -> PortfolioState:
    """Snapshot for the risk limits.

    Day/month P&L = trades closed since the start of the day/month (UTC) plus the current
    unrealized P&L of open positions. Simple and on the cautious side.
    """
    positions = open_positions(session)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    unrealized = sum(p.quantity * (prices[p.symbol] - p.entry_price) - p.entry_fee_usdt for p in positions)
    open_risk = sum(max(0.0, p.quantity * (p.entry_price - p.stop)) for p in positions)
    return PortfolioState(
        open_symbols=frozenset(p.symbol for p in positions),
        open_risk_pct=open_risk / equity * 100,
        day_pnl_pct=(realized_pnl_since(session, day_start) + unrealized) / equity * 100,
        month_pnl_pct=(realized_pnl_since(session, month_start) + unrealized) / equity * 100,
    )


if __name__ == "__main__":
    # Dry run: write a fake signal, position and trade, print them, then roll back (nothing is saved)
    from datetime import UTC, timedelta

    from jevtrade.storage.db import create_db_engine

    now = datetime.now(UTC)
    signal = Signal("BTC/USDT", "trend_pullback", now, entry=85992.0, stop=85136.0, target=87704.0, context={"rsi_14": 47.0})

    with Session(create_db_engine()) as session:
        sig = record_signal(session, signal, regime="TREND", status="opened")
        pos = add_position(session, sig, now, 86035.0, 0.0384, 3.30, entry_order_id="demo-1", oco_order_list_id=None)
        state = portfolio_state(session, equity=10_000.0, prices={"BTC/USDT": 86500.0}, now=now)
        print(f"signal id={sig.id}  position id={pos.id}")
        print(f"state: open={sorted(state.open_symbols)}  open_risk={state.open_risk_pct:.2f}%  day_pnl={state.day_pnl_pct:+.2f}%")

        trade = close_position(session, pos, exit_price=87660.0, exit_fee_usdt=3.37, closed_at=now + timedelta(hours=20), reason="target")
        print(f"trade: pnl={trade.pnl_usdt:+.2f} USDT  R={trade.r_multiple:+.2f}  reason={trade.exit_reason}")
        print(f"open positions after close: {len(open_positions(session))}")
        session.rollback()
    print("rolled back - nothing was saved")
