"""Template for one reusable procurement indicator."""

from typing import Any, Sequence

from bidbox.indicators import IndicatorMetadata, register


@register(IndicatorMetadata("replace_me", "Describe the neutral observation this computes."))
def calculate_replace_me(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return a typed, evidence-neutral observation from company-level records."""
    raise NotImplementedError
