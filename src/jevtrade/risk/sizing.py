"""Position sizing: how much of a coin to buy for a given signal."""

from __future__ import annotations

from dataclasses import dataclass

from jevtrade.strategies.signal import Signal


@dataclass(frozen=True)
class SizingParams:
    risk_per_trade_pct: float = 1.0   # target loss if the stop is hit, % of equity
    max_position_pct: float = 33.0    # one position may use at most this % of equity (3 fit at once)


@dataclass(frozen=True)
class Sizing:
    quantity: float      # how much of the coin to buy (e.g. 0.038 BTC)
    notional: float      # quantity x entry, in USDT
    risk_amount: float   # USDT lost if the stop is hit (before fees)
    risk_pct: float      # risk_amount as % of equity
    capped: bool         # True if max_position_pct limited the size


def position_size(signal: Signal, equity: float, params: SizingParams, risk_pct: float | None = None) -> Sizing:
    """Size a position so that hitting the stop loses `risk_pct` of equity, but never exceed max_position_pct.

    `risk_pct` defaults to params.risk_per_trade_pct; scoring (later) can pass a smaller value like 0.5.
    """
    risk_pct = params.risk_per_trade_pct if risk_pct is None else risk_pct
    wanted_qty = equity * risk_pct / 100 / signal.risk_per_unit
    max_qty = equity * params.max_position_pct / 100 / signal.entry
    quantity = min(wanted_qty, max_qty)
    risk_amount = quantity * signal.risk_per_unit
    return Sizing(
        quantity=quantity,
        notional=quantity * signal.entry,
        risk_amount=risk_amount,
        risk_pct=risk_amount / equity * 100,
        capped=wanted_qty > max_qty,
    )


if __name__ == "__main__":
    params = SizingParams()
    equity = 10_000.0
    examples = [
        Signal("BTC/USDT", "trend_pullback", None, entry=85992.0, stop=85136.0, target=87704.0),
        Signal("SOL/USDT", "breakout", None, entry=119.57, stop=114.00, target=133.50),
    ]
    for s in examples:
        size = position_size(s, equity, params)
        print(
            f"{s.symbol:<9} 1R={s.risk_per_unit:.2f} ({s.risk_per_unit / s.entry * 100:.2f}% of price)  "
            f"qty={size.quantity:.4f}  notional={size.notional:.2f}  "
            f"risk={size.risk_amount:.2f} ({size.risk_pct:.2f}%)  capped={size.capped}"
        )
