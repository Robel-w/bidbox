"""One-shot recovery for known data-quality and comparability failures."""

from audit.logger import append_audit_event


def _benchmark_limitation(output: dict) -> str:
	levels = ", ".join(output.get("classification_levels_tried") or ["exact"])
	return (
		f"Price benchmark not possible: {output.get('comparable_count', 0)} peer award(s) found "
		f"(classification levels tried: {levels}); {output.get('min_comparables', 3)} required. "
		"No price observation is made for this award."
	)


async def recover(state: dict) -> dict:
	"""Record why evidence is insufficient; never relax the comparability threshold."""
	limitations = list(state.get("limitations", []))
	added: list[str] = []
	for item in state.get("tool_outputs", []):
		output = item.get("output") or {}
		if item.get("tool") == "benchmark_award" and output.get("verdict") == "insufficient_comparables":
			added.append(_benchmark_limitation(output))
	for flag in state.get("tender", {}).get("data_quality_flags", []):
		added.append(f"Data-quality flag on the award record: {flag}. Related observations need manual verification.")
	limitations.extend(item for item in added if item not in limitations)
	append_audit_event(
		state["run_id"],
		"agent",
		"recovery",
		{"trigger": "insufficient_comparables_or_data_quality"},
		{"status": "limitations_recorded", "limitations": added},
	)
	return {
		"recovery_attempted": True,
		"recovery_needed": False,
		"limitations": limitations,
		"insufficient_evidence": not state.get("findings"),
	}
