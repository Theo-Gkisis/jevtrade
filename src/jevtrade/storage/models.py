"""Database tables (SQLAlchemy ORM). All table names, columns and stored values are in English."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Engine, ForeignKey, String, func, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SignalRow(Base):
    """Every signal a strategy produced, whether we traded it or not."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[str] = mapped_column(String(30), index=True)
    regime: Mapped[str] = mapped_column(String(10))
    entry: Mapped[float]
    stop: Mapped[float]
    target: Mapped[float]
    context: Mapped[dict] = mapped_column(JSON)              # indicator values at signal time
    status: Mapped[str] = mapped_column(String(10))           # "opened" or "blocked"
    block_reason: Mapped[str | None] = mapped_column(String(500))


class PositionRow(Base):
    """A position that is open right now. Deleted when it closes (the result goes to `trades`)."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    symbol: Mapped[str] = mapped_column(String(20), unique=True)   # one position per coin, enforced by the DB
    strategy: Mapped[str] = mapped_column(String(30))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float]
    quantity: Mapped[float]
    entry_fee_usdt: Mapped[float]
    initial_stop: Mapped[float]      # defines 1R, never changes
    stop: Mapped[float]              # current stop, moves to entry at breakeven
    target: Mapped[float]
    entry_order_id: Mapped[str] = mapped_column(String(40))
    oco_order_list_id: Mapped[int | None] = mapped_column(BigInteger)


class TradeRow(Base):
    """A closed position and its result."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy: Mapped[str] = mapped_column(String(30), index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entry_price: Mapped[float]
    exit_price: Mapped[float]
    quantity: Mapped[float]
    fees_usdt: Mapped[float]
    pnl_usdt: Mapped[float]          # net profit/loss after all fees
    r_multiple: Mapped[float]        # pnl in R, e.g. +1.6 or -1.05
    exit_reason: Mapped[str] = mapped_column(String(20))   # "target", "stop", "breakeven", "time", "news", "monthly_limit"


class JevDecisionRow(Base):
    """Every question sent to Jev for a signal, its answer, and the decision our policy took."""

    __tablename__ = "jev_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    model: Mapped[str | None] = mapped_column(String(60))            # model that answered, None on error
    state: Mapped[dict] = mapped_column(JSON)                        # what we sent
    response: Mapped[dict | None] = mapped_column(JSON)              # full answer, None on error
    jev_regime: Mapped[str | None] = mapped_column(String(10))
    regime_confidence: Mapped[float | None]
    target_probability: Mapped[float | None]
    news_risk: Mapped[float | None]                                  # 0 = none ... 3 = high
    breakout_genuine: Mapped[float | None]                           # only for breakout signals
    action: Mapped[str] = mapped_column(String(10))                  # "approve", "reduce" or "reject"
    risk_multiplier: Mapped[float]
    reasons: Mapped[str | None] = mapped_column(String(1000))
    latency_ms: Mapped[int | None]
    cost_usd: Mapped[float | None]
    error: Mapped[str | None] = mapped_column(String(500))           # why Jev could not be reached


def create_tables(engine: Engine) -> None:
    """Create any missing tables. Existing tables and their data are left untouched."""
    Base.metadata.create_all(engine)
    # Supabase exposes the public schema through its web Data API. Row Level Security with no
    # policies blocks that API completely; the bot connects as the table owner, which bypasses RLS.
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            conn.execute(text(f'alter table "{table.name}" enable row level security'))


if __name__ == "__main__":
    from jevtrade.storage.db import create_db_engine

    engine = create_db_engine()
    create_tables(engine)
    inspector = inspect(engine)
    for table in ("signals", "positions", "trades", "jev_decisions"):
        columns = [c["name"] for c in inspector.get_columns(table)]
        print(f"{table:<10} {len(columns)} columns: {', '.join(columns)}")
