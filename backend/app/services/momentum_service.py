# backend/app/services/momentum_service.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MomentumInputs:
    competitor_name: str

    # Review volume changes
    reviews_delta_1d: int
    reviews_delta_7d: int

    # Rating changes (can be None if not available)
    rating_delta_7d: Optional[float] = None


@dataclass(frozen=True)
class MomentumResult:
    competitor_name: str
    momentum_score: int  # -100..+100
    explanation: str
    label: str  # "accelerating" | "decelerating" | "steady"
    components: dict  # debug-friendly breakdown


def _clamp_int(x: float, lo: int, hi: int) -> int:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return int(round(x))


def compute_competitor_momentum(inp: MomentumInputs) -> MomentumResult:
    """
    Momentum concept (simple + interpretable):
      - Compare short-term review velocity to the 7-day baseline velocity.
      - Add/penalize based on 7-day rating trend.

    Definitions:
      vel_1d = reviews_delta_1d
      vel_7d_avg = reviews_delta_7d / 7

      accel_ratio = (vel_1d - vel_7d_avg) / max(1, vel_7d_avg)
        -> positive = accelerating, negative = decelerating

    Scoring:
      - Velocity component: map accel_ratio into [-80, +80]
      - Rating component: rating_delta_7d mapped into [-20, +20] (if provided)

    Final score: clamp to [-100, +100]
    """
    vel_1d = int(inp.reviews_delta_1d)
    vel_7d = int(inp.reviews_delta_7d)

    vel_7d_avg = vel_7d / 7.0
    denom = max(1.0, vel_7d_avg)
    accel_ratio = (vel_1d - vel_7d_avg) / denom  # ~ -inf..+inf, but usually -1..+3

    # Velocity component: cap accel_ratio to keep output stable and readable
    # -2.0 => strong deceleration, +2.0 => strong acceleration
    accel_ratio_capped = max(-2.0, min(2.0, accel_ratio))
    velocity_component = accel_ratio_capped * 40.0  # -> [-80..+80]

    # Rating component (optional)
    rating_component = 0.0
    rating_delta_7d = inp.rating_delta_7d
    if rating_delta_7d is not None:
        # cap rating delta to +/-0.5 over 7 days (big moves are rare)
        rd = max(-0.5, min(0.5, float(rating_delta_7d)))
        rating_component = (rd / 0.5) * 20.0  # -> [-20..+20]

    raw_score = velocity_component + rating_component
    score = _clamp_int(raw_score, -100, 100)

    if score >= 20:
        label = "accelerating"
    elif score <= -20:
        label = "decelerating"
    else:
        label = "steady"

    # Explanation: short + business-friendly
    # Mention acceleration in reviews, and rating trend if meaningful.
    vel_phrase = ""
    if vel_7d_avg < 1:
        # baseline very low; frame as “today vs low baseline”
        if vel_1d > 0:
            vel_phrase = f"picked up {vel_1d} new review(s) today after a low weekly baseline"
        else:
            vel_phrase = "showed little review activity today and over the past week"
    else:
        if accel_ratio > 0.25:
            vel_phrase = f"accelerated in reviews today ({vel_1d} vs ~{vel_7d_avg:.1f}/day baseline)"
        elif accel_ratio < -0.25:
            vel_phrase = f"slowed in reviews today ({vel_1d} vs ~{vel_7d_avg:.1f}/day baseline)"
        else:
            vel_phrase = f"held a steady review pace today ({vel_1d} vs ~{vel_7d_avg:.1f}/day baseline)"

    rating_phrase = ""
    if rating_delta_7d is not None:
        if rating_delta_7d >= 0.10:
            rating_phrase = f" and improved rating by {rating_delta_7d:+.2f} over 7 days"
        elif rating_delta_7d <= -0.10:
            rating_phrase = f" and saw rating slip {rating_delta_7d:+.2f} over 7 days"

    explanation = f"{inp.competitor_name} {vel_phrase}{rating_phrase}."

    return MomentumResult(
        competitor_name=inp.competitor_name,
        momentum_score=score,
        explanation=explanation,
        label=label,
        components={
            "vel_1d": vel_1d,
            "vel_7d": vel_7d,
            "vel_7d_avg": vel_7d_avg,
            "accel_ratio": accel_ratio,
            "velocity_component": velocity_component,
            "rating_delta_7d": rating_delta_7d,
            "rating_component": rating_component,
            "raw_score": raw_score,
        },
    )
