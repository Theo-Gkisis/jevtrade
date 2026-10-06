"""Jev (TypeSafe "System One") via OpenRouter: send a signal's state, get typed decisions back."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx
import pandas as pd
from dotenv import load_dotenv

from jevtrade.regime.detector import bb_width_percentile, btc_drop_pct, channel_width_pct
from jevtrade.strategies.signal import Signal

# Verified 2026-10-06: this URL works; the /api/v1/api/alpha/decisions variant in the docs returns 404
JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
JEV_REGIMES = ("TREND", "RANGE", "SQUEEZE", "DOWNTREND", "CHAOS")
NEWS_LEVELS = ("none", "low", "medium", "high")

TRADER_CONTEXT = (
    "Context: a rules-based swing trading bot on Binance spot. It only buys (long only, no leverage, "
    "no shorting) and decides once per hour, at the close of each 1h candle. Every position has a stop loss "
    "and a take profit placed at the exchange, and is closed at market after `max_holding_hours` if neither "
    "was hit. All prices are in USDT, all times in UTC, all lists are ordered oldest first."
)


def build_questions(is_breakout: bool) -> dict:
    """The questions we ask Jev, in its typed format (choice / noul / score)."""
    questions = {
        "regime": {
            "type": "choice",
            "instructions": (
                f"{TRADER_CONTEXT} Classify the CURRENT market regime of `symbol`. Form your own view from "
                "`indicators_1h`, `indicators_4h`, `derived` and the shape of the recent highs, lows and closes "
                "on both timeframes. Weigh the 4h structure most for direction and strength, and the 1h data for "
                "how volatile and orderly the market is right now. Pick the single best fit."
            ),
            "criteria": {
                "TREND": (
                    "A sustained, orderly UPtrend. Typical evidence: the 4h close is clearly above the 4h ema_50 "
                    "(price_vs_ema50_4h_pct positive), adx_14 on 4h above roughly 20, and recent_highs_4h / "
                    "recent_lows_4h making higher highs and higher lows. Short dips back toward the 1h ema_20 are "
                    "normal inside a trend and do not break it."
                ),
                "RANGE": (
                    "Sideways, directionless movement. Typical evidence: 4h adx_14 below roughly 20, price "
                    "oscillating between a recognisable floor and ceiling for two days or more (range_48h_pct "
                    "moderate), closes crossing ema_20 and ema_50 back and forth without follow-through, and no "
                    "clear sequence of higher highs or lower lows."
                ),
                "SQUEEZE": (
                    "Volatility unusually COMPRESSED compared with the past month, before a likely large move whose "
                    "direction is not yet decided. Typical evidence: bb_width_rank_30d near the bottom (roughly "
                    "under 15), low atr_pct, small overlapping 1h candles, and a tight range_48h_pct for this coin."
                ),
                "DOWNTREND": (
                    "A sustained, orderly DOWNtrend. Typical evidence: the 4h close clearly below the 4h ema_50 "
                    "(price_vs_ema50_4h_pct negative), adx_14 on 4h above roughly 20, and lower highs and lower "
                    "lows on the 4h chart. Bounces fail below falling moving averages."
                ),
                "CHAOS": (
                    "Disorderly, crash-like or news-driven conditions where opening a long is dangerous. Typical "
                    "evidence: drops of several percent within a few hours (max_drop_from_4h_high_pct large), 1h "
                    "candles many times larger than the ATR, violent reversals, panic selling or sudden spikes."
                ),
            },
        },
        "target_before_stop": {
            "type": "noul",
            "instructions": (
                f"{TRADER_CONTEXT} A LONG position would be opened at `entry`, at the close of the latest 1h "
                "candle. It exits with a loss at `stop` or with a profit at `target`, whichever price is touched "
                "first; if neither is touched within `max_holding_hours` it is closed at market. Distances are given "
                "in percent and in ATRs in `derived`. Judging the trend, momentum, support and resistance, volume "
                "and the recent price path: will the price touch `target` BEFORE it touches `stop`, within that time?"
            ),
            "criteria": {
                "true": (
                    "The evidence supports a rise of target_distance_atr ATRs before a fall of stop_distance_atr "
                    "ATRs. For example: the higher-timeframe trend is intact or the price sits at a well-defined "
                    "floor; the latest dip is holding at support (1h ema_20, the range floor or a recent swing low); "
                    "rsi_14 is recovering rather than collapsing; volume supports the buyers; the last candle closed "
                    "strong with little upper wick; the stop sits below a logical support level; and the target is "
                    "reachable within the time limit without first breaking through obvious resistance."
                ),
                "false": (
                    "The stop is more likely to be touched first, or the trade is unlikely to reach the target in "
                    "time. For example: momentum is fading or turning down; the price is breaking below support or "
                    "printing lower highs; the target lies beyond recent highs that act as resistance or is too far "
                    "for the time limit; volume does not confirm the move; the last candle shows strong selling "
                    "(long upper wick or a large red body); or the stop is so close that normal noise would hit it."
                ),
            },
        },
        "news_risk": {
            "type": "score",
            "instructions": (
                f"{TRADER_CONTEXT} Rate the DOWNSIDE risk to a long position in `symbol` over the next "
                "`max_holding_hours` hours that comes from `headlines` (recent news titles, newest last). Consider "
                "news about this coin and news that hits the whole crypto market. Judge only from the headlines "
                "given. If `headlines` is empty, the answer is None."
            ),
            "criteria": [
                "None: no headlines, or the headlines are neutral, positive, or unrelated to this coin and to the "
                "crypto market as a whole.",
                "Low: minor negative items unlikely to move the price materially, such as routine regulatory "
                "discussion, small project issues, or general bearish price commentary.",
                "Medium: notable negative news for this coin or the whole market that could move the price several "
                "percent, such as exchange outages or withdrawal problems, large liquidations, heavy ETF outflows, "
                "enforcement action against a major company, or a large token unlock.",
                "High: a severe negative event likely to cause a sharp drop, such as a hack or exploit, an exchange "
                "or lender insolvency, a ban or delisting, a stablecoin losing its peg, or major legal action "
                "against this project.",
            ],
        },
    }
    if is_breakout:
        questions["breakout_genuine"] = {
            "type": "noul",
            "instructions": (
                f"{TRADER_CONTEXT} After a quiet period, the latest 1h candle closed above `setup_context.quiet_high`, "
                "the highest price of the previous 24 hours. Is this a GENUINE breakout that will keep going up, "
                "rather than a false breakout that soon falls back below quiet_high?"
            ),
            "criteria": {
                "true": (
                    "A decisive breakout: the close is clearly above quiet_high (not by a hair); the candle closed "
                    "near its high with a small upper wick; volume_vs_average is at least 1.5 and preferably much "
                    "higher; the quiet period before it was genuinely tight (low bb_width_rank_30d); and the 4h "
                    "trend is not pointing down."
                ),
                "false": (
                    "A likely false breakout: the close is only marginally above quiet_high; a long upper wick shows "
                    "sellers pushing the price back; volume is weak or barely above average; the move goes against a "
                    "falling 4h trend; or the price is already stretched far above ema_20."
                ),
            },
        }
    return questions


def _r(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _pct(a: float, b: float) -> float:
    """How many percent `a` is above (+) or below (-) `b`."""
    return round((float(a) - float(b)) / float(b) * 100, 3)


def derived_features(signal: Signal, df_1h: pd.DataFrame, df_4h: pd.DataFrame, max_holding_hours: int) -> dict:
    """Pre-computed relationships that are easier to judge than raw numbers."""
    h1, h4 = df_1h.iloc[-1], df_4h.iloc[-1]
    atr = float(h1["atr_14"])
    band = float(h1["bb_upper"] - h1["bb_lower"])
    return {
        "max_holding_hours": max_holding_hours,
        "stop_distance_pct": _pct(signal.entry, signal.stop),
        "stop_distance_atr": _r((signal.entry - signal.stop) / atr, 2),
        "target_distance_pct": _pct(signal.target, signal.entry),
        "target_distance_atr": _r((signal.target - signal.entry) / atr, 2),
        "reward_risk": _r(signal.reward_risk, 2),
        "atr_pct": _r(atr / h1["close"] * 100, 3),
        "price_vs_ema20_1h_pct": _pct(h1["close"], h1["ema_20"]),
        "price_vs_ema50_4h_pct": _pct(h4["close"], h4["ema_50"]),
        "bb_position": _r((h1["close"] - h1["bb_lower"]) / band, 2) if band else None,
        "bb_width_rank_30d": _r(bb_width_percentile(df_1h, 720), 1),
        "range_48h_pct": _r(channel_width_pct(df_1h, 48), 2),
        "change_24h_pct": _pct(h1["close"], df_1h["close"].iloc[-25]),
        "max_drop_from_4h_high_pct": _r(btc_drop_pct(df_1h, 4).tail(12).max(), 2),
    }


def last_candle_shape(df_1h: pd.DataFrame) -> dict:
    """Anatomy of the latest 1h candle: direction, body and wicks as % of its range, size in ATRs."""
    c = df_1h.iloc[-1]
    o, h, l, cl = (float(c[k]) for k in ("open", "high", "low", "close"))
    rng = h - l or 1e-12
    return {
        "open": _r(o), "high": _r(h), "low": _r(l), "close": _r(cl),
        "direction": "green" if cl > o else "red" if cl < o else "flat",
        "body_pct_of_range": _r(abs(cl - o) / rng * 100, 1),
        "upper_wick_pct_of_range": _r((h - max(o, cl)) / rng * 100, 1),
        "lower_wick_pct_of_range": _r((min(o, cl) - l) / rng * 100, 1),
        "range_in_atr": _r(rng / float(c["atr_14"]), 2),
    }


def build_state(
    signal: Signal,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    headlines: list[str] | None = None,
    max_holding_hours: int = 48,
) -> dict:
    """Structured snapshot of the proposed trade and the market that Jev judges.

    Deliberately leaves out our rules' regime and the strategy name, so Jev forms an
    independent opinion instead of echoing ours.
    Expects df_1h with ema_20, rsi_14, atr_14, Bollinger columns, volume_ma_20 and df_4h with ema_50, adx_14.
    """
    h1, h4 = df_1h.iloc[-1], df_4h.iloc[-1]
    last_24h, last_12x4h = df_1h.tail(24), df_4h.tail(12)
    return {
        "symbol": signal.symbol,
        "candle_time_utc": signal.candle_time.isoformat(),
        "trade": {"side": "long", "entry": _r(signal.entry), "stop": _r(signal.stop), "target": _r(signal.target)},
        "derived": derived_features(signal, df_1h, df_4h, max_holding_hours),
        "last_candle_1h": last_candle_shape(df_1h),
        "indicators_1h": {
            "close": _r(h1["close"]),
            "ema_20": _r(h1["ema_20"]),
            "rsi_14": _r(h1["rsi_14"], 1),
            "atr_14": _r(h1["atr_14"]),
            "bb_lower": _r(h1["bb_lower"]),
            "bb_middle": _r(h1["bb_middle"]),
            "bb_upper": _r(h1["bb_upper"]),
            "bb_width_pct": _r(h1["bb_width"], 2),
            "volume_vs_average": _r(h1["volume"] / h1["volume_ma_20"], 2),
        },
        "indicators_4h": {"close": _r(h4["close"]), "ema_50": _r(h4["ema_50"]), "adx_14": _r(h4["adx_14"], 1)},
        "recent_1h": {
            "closes": [_r(x) for x in last_24h["close"]],
            "highs": [_r(x) for x in last_24h["high"]],
            "lows": [_r(x) for x in last_24h["low"]],
            "rsi_14": [_r(x, 1) for x in last_24h["rsi_14"].tail(6)],
            "volume_vs_average": [_r(v / m, 2) for v, m in zip(last_24h["volume"].tail(6), last_24h["volume_ma_20"].tail(6))],
        },
        "recent_4h": {
            "closes": [_r(x) for x in last_12x4h["close"]],
            "highs": [_r(x) for x in last_12x4h["high"]],
            "lows": [_r(x) for x in last_12x4h["low"]],
        },
        "setup_context": {k: _r(v) for k, v in signal.context.items() if k == "quiet_high"},
        "headlines": headlines or [],
        "field_notes": {
            "recent_1h": "the last 24 hourly candles; rsi_14 and volume_vs_average cover only the last 6",
            "recent_4h": "the last 12 four-hour candles (2 days)",
            "volume_vs_average": "candle volume divided by the 20-candle average volume",
            "bb_position": "0 = at the lower Bollinger band, 1 = at the upper band",
            "bb_width_rank_30d": "0 = the narrowest Bollinger width of the last 30 days, 100 = the widest",
            "range_48h_pct": "(highest high - lowest low) of the last 48 hours, as % of the low",
            "max_drop_from_4h_high_pct": "largest drop from a 4-hour high during the last 12 hours, in %",
            "price_vs_*_pct": "how far the close is above (+) or below (-) that moving average, in %",
        },
    }


@dataclass(frozen=True)
class JevAssessment:
    regime: str                       # Jev's choice: TREND / RANGE / SQUEEZE / DOWNTREND / CHAOS
    regime_confidence: float
    regime_probabilities: dict[str, float]
    target_probability: float         # probability the target is hit before the stop
    news_risk: float                  # 0 = none ... 3 = high
    breakout_genuine: float | None    # only for breakout signals
    raw: dict                         # the full API response, kept for the journal
    latency_ms: int
    cost_usd: float


class JevClient:
    def __init__(self, api_key: str | None = None, model: str = JEV_MODEL, timeout_s: float = 20.0) -> None:
        load_dotenv()
        self.model = model
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY missing (check .env)")
        self._http = httpx.Client(timeout=timeout_s)

    def assess(self, state: dict, is_breakout: bool = False) -> JevAssessment:
        body = {"model": self.model, "state": state, "questions": build_questions(is_breakout)}
        started = time.perf_counter()
        response = self._http.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {self._api_key}"})
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        data = response.json()
        answers = data["answers"]
        return JevAssessment(
            regime=answers["regime"]["choice"],
            regime_confidence=answers["regime"]["confidence"],
            regime_probabilities=answers["regime"]["probabilities"],
            target_probability=answers["target_before_stop"]["noul"],
            news_risk=answers["news_risk"]["score"],
            breakout_genuine=answers["breakout_genuine"]["noul"] if is_breakout else None,
            raw=data,
            latency_ms=latency_ms,
            cost_usd=data["usage"]["cost"],
        )


if __name__ == "__main__":
    # Ask Jev about a "what if" Trend Pullback entry on each coin right now (not real signals)
    from jevtrade.data.candles import create_exchange
    from jevtrade.regime.detector import RegimeParams, detect_regime
    from jevtrade.run import load_market_data
    from jevtrade.strategies.trend_pullback import TrendPullbackParams, compute_levels

    market = create_exchange()
    client = JevClient()
    btc_1h = load_market_data(market, "BTC/USDT")[0]
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        df_1h, df_4h = load_market_data(market, symbol)
        rules_regime = detect_regime(symbol, btc_1h, df_1h, df_4h, RegimeParams())
        entry, stop, target = compute_levels(df_1h, TrendPullbackParams())
        signal = Signal(symbol, "trend_pullback", df_1h.index[-1].to_pydatetime(), entry, stop, target)
        a = client.assess(build_state(signal, df_1h, df_4h))
        probs = " ".join(f"{k}={v:.2f}" for k, v in a.regime_probabilities.items())
        print(f"{symbol:<9} rules={rules_regime:<8} Jev={a.regime:<9} ({a.regime_confidence:.2f})  [{probs}]")
        print(f"          target_before_stop={a.target_probability:.2f}  news_risk={a.news_risk:.2f}/3  "
              f"tokens={a.raw['usage']['input_tokens']}  cost=${a.cost_usd:.6f}  {a.latency_ms} ms")
