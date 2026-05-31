from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ThreatInputs:
    competitor_name: str
    momentum_score: int
    velocity_ratio: float
    reviews_delta_7d: int


@dataclass
class ThreatResult:
    competitor_name: str
    threat_score: int
    threat_level: str
    reasons: List[str]


def compute_threat(inputs: ThreatInputs) -> ThreatResult:
    """
    Rule-based threat scoring.

    Philosophy:
    - Momentum indicates acceleration
    - Velocity ratio indicates sudden behavior change
    - Review growth shows sustained pressure

    Score bands:
      0–29  → low
      30–59 → medium
      60+   → high
    """
    score = 0
    reasons: List[str] = []

    # Momentum contribution
    if inputs.momentum_score >= 30:
        score += 25
        reasons.append("Strong accelerating momentum")
    elif inputs.momentum_score >= 10:
        score += 15
        reasons.append("Moderate upward momentum")

    # Velocity anomaly
    if inputs.velocity_ratio >= 3.0:
        score += 25
        reasons.append("Sudden spike in review activity")
    elif inputs.velocity_ratio >= 1.5:
        score += 15
        reasons.append("Above-normal review velocity")

    # Sustained growth
    if inputs.reviews_delta_7d >= 10:
        score += 25
        reasons.append("Strong 7-day review growth")
    elif inputs.reviews_delta_7d >= 5:
        score += 15
        reasons.append("Moderate 7-day review growth")

    # Clamp
    score = min(score, 100)

    if score >= 60:
        level = "high"
    elif score >= 30:
        level = "medium"
    else:
        level = "low"

    if not reasons:
        reasons.append("No competitive pressure detected")

    return ThreatResult(
        competitor_name=inputs.competitor_name,
        threat_score=score,
        threat_level=level,
        reasons=reasons,
    )
