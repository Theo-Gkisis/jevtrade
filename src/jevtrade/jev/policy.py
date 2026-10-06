"""Turn Jev's answers into a decision. Jev can only veto or shrink a trade, never open or enlarge one."""

from __future__ import annotations

from dataclasses import dataclass

from jevtrade.jev.client import JevAssessment


@dataclass(frozen=True)
class JevPolicyParams:
    min_target_probability: float = 0.55   # below this: reject
    full_size_probability: float = 0.65    # between min and this: reduce
    reduced_multiplier: float = 0.5        # risk multiplier when reducing
    max_news_risk: float = 2.5             # news score 0-3; at or above this ("high"): reject
    min_breakout_genuine: float = 0.5      # breakout signals: below this probability it is "fake": reject


@dataclass(frozen=True)
class JevDecision:
    action: str               # "approve", "reduce" or "reject"
    risk_multiplier: float    # applied to the position's risk; always between 0 and 1
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        # Hard guarantee: Jev can never increase risk
        if not 0.0 <= self.risk_multiplier <= 1.0:
            raise ValueError(f"risk_multiplier must be between 0 and 1, got {self.risk_multiplier}")


def decide(
    assessment: JevAssessment | None, rules_regime: str, is_breakout: bool, params: JevPolicyParams
) -> JevDecision:
    """Apply the Jev rules. `assessment` is None when Jev could not be reached (fail-closed: reject)."""
    if assessment is None:
        return JevDecision("reject", 0.0, ("jev unavailable (fail-closed)",))

    reasons = []
    if assessment.regime != rules_regime:
        reasons.append(
            f"regime disagreement: rules={rules_regime}, jev={assessment.regime} ({assessment.regime_confidence:.2f})"
        )
    if assessment.target_probability < params.min_target_probability:
        reasons.append(
            f"target probability {assessment.target_probability:.2f} below {params.min_target_probability}"
        )
    if assessment.news_risk >= params.max_news_risk:
        reasons.append(f"high news risk ({assessment.news_risk:.2f}/3)")
    if is_breakout and (assessment.breakout_genuine is None or assessment.breakout_genuine < params.min_breakout_genuine):
        reasons.append(f"likely false breakout ({assessment.breakout_genuine})")
    if reasons:
        return JevDecision("reject", 0.0, tuple(reasons))

    if assessment.target_probability < params.full_size_probability:
        return JevDecision(
            "reduce",
            params.reduced_multiplier,
            (f"target probability {assessment.target_probability:.2f} below {params.full_size_probability}",),
        )
    return JevDecision("approve", 1.0, ())


if __name__ == "__main__":
    # Made-up Jev answers (no API calls) to show every rule
    def fake(regime="TREND", p=0.70, news=0.0, breakout=None) -> JevAssessment:
        return JevAssessment(regime, 0.80, {}, p, news, breakout, raw={}, latency_ms=0, cost_usd=0.0)

    params = JevPolicyParams()
    scenarios = [
        ("all good", fake(), "TREND", False),
        ("unsure (0.60)", fake(p=0.60), "TREND", False),
        ("low probability", fake(p=0.45), "TREND", False),
        ("regime disagrees", fake(regime="RANGE"), "TREND", False),
        ("Jev sees DOWNTREND", fake(regime="DOWNTREND", p=0.30), "TREND", False),
        ("high news risk", fake(news=2.8), "TREND", False),
        ("fake breakout", fake(regime="SQUEEZE", breakout=0.35), "SQUEEZE", True),
        ("genuine breakout", fake(regime="SQUEEZE", breakout=0.80), "SQUEEZE", True),
        ("Jev unreachable", None, "TREND", False),
    ]
    for name, assessment, rules_regime, is_breakout in scenarios:
        d = decide(assessment, rules_regime, is_breakout, params)
        print(f"{name:<20} -> {d.action:<8} x{d.risk_multiplier:.1f}  {'; '.join(d.reasons)}")
