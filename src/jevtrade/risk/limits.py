"""Risk limits: may we open a new position right now?"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LimitParams:
    max_open_positions: int = 3
    max_total_risk_pct: float = 2.0       # sum of the risk of all open positions, % of equity
    daily_loss_limit_pct: float = 3.0     # no new positions for the rest of the day (UTC)
    monthly_loss_limit_pct: float = 8.0   # stop completely until a manual restart


@dataclass(frozen=True)
class PortfolioState:
    open_symbols: frozenset[str]   # coins we already hold
    open_risk_pct: float           # total risk of open positions, % of equity
    day_pnl_pct: float             # today's profit/loss, % of equity (negative = loss)
    month_pnl_pct: float           # this month's profit/loss, % of equity


def check_limits(symbol: str, new_risk_pct: float, state: PortfolioState, params: LimitParams) -> list[str]:
    """Return the reasons a new position in `symbol` is NOT allowed. An empty list means allowed."""
    reasons = []
    if state.month_pnl_pct <= -params.monthly_loss_limit_pct:
        reasons.append(f"monthly loss {state.month_pnl_pct:.2f}% hit the -{params.monthly_loss_limit_pct}% limit")
    if state.day_pnl_pct <= -params.daily_loss_limit_pct:
        reasons.append(f"daily loss {state.day_pnl_pct:.2f}% hit the -{params.daily_loss_limit_pct}% limit")
    if symbol in state.open_symbols:
        reasons.append(f"already holding {symbol}")
    if len(state.open_symbols) >= params.max_open_positions:
        reasons.append(f"already {len(state.open_symbols)} open positions (max {params.max_open_positions})")
    if state.open_risk_pct + new_risk_pct > params.max_total_risk_pct:
        reasons.append(
            f"total risk would be {state.open_risk_pct + new_risk_pct:.2f}% (max {params.max_total_risk_pct}%)"
        )
    return reasons


if __name__ == "__main__":
    params = LimitParams()
    scenarios = [
        ("empty account", "BTC/USDT", PortfolioState(frozenset(), 0.0, 0.0, 0.0)),
        ("already hold BTC", "BTC/USDT", PortfolioState(frozenset({"BTC/USDT"}), 0.33, 0.0, 0.0)),
        ("3 positions open", "BTC/USDT", PortfolioState(frozenset({"ETH/USDT", "SOL/USDT", "XRP/USDT"}), 0.9, 0.0, 0.0)),
        ("risk almost full", "BTC/USDT", PortfolioState(frozenset({"ETH/USDT", "SOL/USDT"}), 1.8, 0.0, 0.0)),
        ("bad day", "BTC/USDT", PortfolioState(frozenset(), 0.0, -3.2, -3.2)),
        ("bad month", "BTC/USDT", PortfolioState(frozenset(), 0.0, -0.5, -8.5)),
    ]
    for name, symbol, state in scenarios:
        reasons = check_limits(symbol, new_risk_pct=0.33, state=state, params=params)
        verdict = "ALLOWED" if not reasons else "BLOCKED: " + "; ".join(reasons)
        print(f"{name:<18} {verdict}")
