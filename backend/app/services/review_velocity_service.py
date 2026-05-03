# app/services/review_velocity_service.py

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class ReviewVelocityResult(BaseModel):
    competitor_name: str
    velocity_1d: int
    velocity_7d_avg: float
    velocity_ratio: float
    anomaly: str  # "spike" | "drop" | "normal"
    explanation: str


class ReviewVelocityInputs(BaseModel):
    competitor_name: str
    reviews_delta_1d: int
    reviews_delta_7d: int


def compute_review_velocity_trend(
    inputs: ReviewVelocityInputs,
    spike_threshold: float = 2.0,
    drop_threshold: float = 0.4,
) -> ReviewVelocityResult:
    """
    Compare short-term vs long-term review velocity.

    Definitions:
      velocity_1d     = reviews added in last 1 day
      velocity_7d_avg = reviews added per day over last 7 days

    anomaly logic:
      - spike  → velocity_ratio >= spike_threshold
      - drop   → velocity_ratio <= drop_threshold (and 7d avg > 0)
      - normal → otherwise
    """

    name = inputs.competitor_name
    v1d = max(int(inputs.reviews_delta_1d or 0), 0)
    v7d = max(int(inputs.reviews_delta_7d or 0), 0)

    v7d_avg = round(v7d / 7.0, 2) if v7d > 0 else 0.0

    # Avoid divide-by-zero explosions
    if v7d_avg <= 0:
        velocity_ratio = float(v1d) if v1d > 0 else 0.0
    else:
        velocity_ratio = round(v1d / v7d_avg, 2)

    # Classification
    if v7d_avg > 0 and velocity_ratio >= spike_threshold:
        anomaly = "spike"
        explanation = (
            f"{name} saw an unusual surge in reviews today compared to its recent average."
        )
    elif v7d_avg > 0 and velocity_ratio <= drop_threshold:
        anomaly = "drop"
        explanation = (
            f"{name}'s review activity dropped sharply compared to the past week."
        )
    else:
        anomaly = "normal"
        explanation = (
            f"{name}'s review activity is consistent with its recent trend."
        )

    return ReviewVelocityResult(
        competitor_name=name,
        velocity_1d=v1d,
        velocity_7d_avg=v7d_avg,
        velocity_ratio=velocity_ratio,
        anomaly=anomaly,
        explanation=explanation,
    )
