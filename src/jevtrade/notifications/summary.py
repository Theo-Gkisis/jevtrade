"""Daily summary of the bot's last 24 hours, built only from what is already stored in the database."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jevtrade.storage.models import CycleRow, JevDecisionRow, PositionRow, SignalRow, TradeRow


def _first_equity_since(session: Session, since: datetime) -> float | None:
    query = (
        select(CycleRow.equity_usdt)
        .where(CycleRow.ran_at >= since, CycleRow.equity_usdt.is_not(None))
        .order_by(CycleRow.ran_at)
        .limit(1)
    )
    return session.scalar(query)


def _pct_change(now: float | None, before: float | None) -> str:
    if now is None or not before:
        return "n/a"
    return f"{(now - before) / before * 100:+.2f}%"


def build_daily_summary(session: Session, now: datetime) -> str:
    """Text for Telegram covering the 24 hours before `now` (UTC)."""
    since = now - timedelta(hours=24)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    cycles = list(session.scalars(select(CycleRow).where(CycleRow.ran_at >= since).order_by(CycleRow.ran_at)))
    latest = next((c for c in reversed(cycles) if c.equity_usdt is not None), None)
    equity = latest.equity_usdt if latest else None
    failed = sum(1 for c in cycles if c.error)

    trades = list(session.scalars(select(TradeRow).where(TradeRow.closed_at >= since)))
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    total_r = sum(t.r_multiple for t in trades)
    total_pnl = sum(t.pnl_usdt for t in trades)

    signals = Counter(session.scalars(select(SignalRow.status).where(SignalRow.created_at >= since)))
    jev = list(session.scalars(select(JevDecisionRow).where(JevDecisionRow.created_at >= since)))
    jev_cost = sum(j.cost_usd or 0.0 for j in jev)
    jev_errors = sum(1 for j in jev if j.error)
    open_symbols = sorted(session.scalars(select(PositionRow.symbol)))

    regimes = " · ".join(f"{s.split('/')[0]} {r}" for s, r in (latest.regimes or {}).items()) if latest else "n/a"
    equity_text = f"{equity:,.2f} USDT" if equity is not None else "n/a"

    return "\n".join(
        [
            f"📊 Daily summary · {now:%Y-%m-%d %H:%M} UTC (last 24h)",
            f"Equity {equity_text} ({_pct_change(equity, _first_equity_since(session, since))} 24h · "
            f"{_pct_change(equity, _first_equity_since(session, month_start))} this month)",
            f"Trades closed: {len(trades)} ({wins} win / {len(trades) - wins} loss) · "
            f"{total_r:+.2f}R · {total_pnl:+,.2f} USDT",
            f"Open positions: {len(open_symbols)}" + (f" ({', '.join(open_symbols)})" if open_symbols else ""),
            f"Signals: {sum(signals.values())} → {signals['opened']} opened · {signals['blocked']} blocked · "
            f"{signals['jev_reject']} rejected by Jev",
            f"Jev: {len(jev)} calls · ${jev_cost:.4f}" + (f" · {jev_errors} errors" if jev_errors else ""),
            f"Cycles: {len(cycles) - failed}/{len(cycles)} OK" + (f" · {failed} failed" if failed else ""),
            f"Regimes now: {regimes}",
        ]
    )


if __name__ == "__main__":
    # Build the summary from the real database and send it to Telegram
    import sys
    from datetime import UTC

    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console default (cp1252) cannot print emoji

    from jevtrade.notifications.telegram import TelegramNotifier
    from jevtrade.storage.db import create_db_engine

    with Session(create_db_engine()) as session:
        text = build_daily_summary(session, datetime.now(UTC))
    print(text)
    TelegramNotifier().notify(text)
