from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RecommendedAction = Literal["call_now", "verify", "skip"]

# Signs the model should look for (plan step 8); responses may include other short labels.
DISTRESS_SIGN_CATEGORIES: frozenset[str] = frozenset(
    {
        "boarded windows",
        "broken windows",
        "overgrown grass",
        "roof damage",
        "roof tarp",
        "peeling paint",
        "trash/debris",
        "abandoned vehicles",
        "broken fence",
        "vacancy signs",
        "general neglect",
    }
)

MIN_CONFIDENCE_FOR_DISTRESS = 70


@dataclass(frozen=True)
class PropertyVerificationResult:
    """AI Street View verification + optional distress scoring."""

    target_confidence: int
    """0–100: target house visible and reasonably centered/clear."""

    distress_score: int
    """0–100; must be 0 when target_confidence < MIN_CONFIDENCE_FOR_DISTRESS."""

    visible_signs: tuple[str, ...]
    condition_summary: str
    recommended_action: RecommendedAction
