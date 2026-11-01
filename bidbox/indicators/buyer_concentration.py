"""Buyer concentration indicator for company-level supplier history."""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from .registry import IndicatorMetadata, register


@dataclass(frozen=True)
class BuyerConcentration:
    """Top-buyer share and neutral distribution context."""

    award_count: int
    share: float
    top_buyer: str | None
    pattern: str


@register(IndicatorMetadata("buyer_concentration", "Share of company awards associated with its most frequent buyer."))
def calculate_buyer_concentration(awards: Sequence[dict[str, Any]]) -> BuyerConcentration:
    """Calculate concentration without ranking suppliers or making an award decision."""
    if not awards:
        return BuyerConcentration(0, 0.0, None, "No historical awards found")
    counts: Counter[str] = Counter(
        str(award.get("buyer_name") or award.get("buyer_id") or "Unspecified Buyer")
        for award in awards
    )
    top_buyer, top_count = counts.most_common(1)[0]
    share = round(top_count / len(awards), 3)
    if share >= 0.70:
        pattern = f"High concentration: {share * 100:.1f}% of awards with {top_buyer}"
    else:
        pattern = "Diversified distribution across buyers"
    return BuyerConcentration(len(awards), share, top_buyer, pattern)
