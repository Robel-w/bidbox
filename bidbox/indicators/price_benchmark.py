"""Robust price benchmark indicator used by the benchmark MCP tool."""

from dataclasses import dataclass
from typing import Any, Sequence

from .registry import IndicatorMetadata, register


@dataclass(frozen=True)
class PriceBenchmark:
    """Robust statistics and verdict for one award against peer prices."""

    comparable_count: int
    median: float | None
    mad: float | None
    modified_z_score: float | None
    iqr_band: tuple[float, float] | None
    verdict: str


def _median(values: list[float]) -> float:
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / 2.0


@register(IndicatorMetadata("price_benchmark", "Robust median, MAD, modified Z-score, and IQR price check."))
def calculate_price_benchmark(
    target_amount: float,
    comparables: Sequence[dict[str, Any]],
    min_comparables: int = 3,
) -> PriceBenchmark:
    """Calculate an objective price verdict without naming or ranking suppliers."""
    count = len(comparables)
    if count < min_comparables:
        return PriceBenchmark(count, None, None, None, None, "insufficient_comparables")
    prices = sorted(float(item["amount"]) for item in comparables)
    median = _median(prices)
    deviations = sorted(abs(price - median) for price in prices)
    mad = _median(deviations)
    modified_z = round(0.6745 * (target_amount - median) / mad, 3) if mad else 0.0
    q1 = prices[int(0.25 * count)]
    q3 = prices[int(0.75 * count)]
    iqr = q3 - q1
    band = (round(q1 - 1.5 * iqr, 2), round(q3 + 1.5 * iqr, 2))
    if modified_z > 3.5 or target_amount > band[1]:
        verdict = "outlier_high"
    elif modified_z < -3.5 or target_amount < band[0]:
        verdict = "outlier_low"
    else:
        verdict = "normal"
    return PriceBenchmark(count, round(median, 2), round(mad, 2), modified_z, band, verdict)
