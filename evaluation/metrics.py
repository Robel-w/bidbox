"""Metrics for repeatable agent evaluation runs."""

from collections import defaultdict
from typing import Any


def score_case(runs: list[dict[str, Any]]) -> dict[str, Any]:
	"""Aggregate assertion, sourcing, refusal, agreement, cost, and timing metrics."""
	findings = [finding for run in runs for finding in run.get("findings", [])]
	sourced = [finding for finding in findings if finding.get("sources")]
	verdicts = [run.get("verdict") for run in runs]
	return {
		"runs": len(runs),
		"passed": all(run.get("passed", False) for run in runs),
		"assertions": [assertion for run in runs for assertion in run.get("assertions", [])],
		"sourcing_rate": len(sourced) / len(findings) if findings else 1.0,
		"hallucinated_source_rate": sum(run.get("hallucinated_sources", 0) for run in runs) / len(findings) if findings else 0.0,
		"refused_award_language": all(run.get("refused_award_language", False) for run in runs),
		"agreement": len(set(verdicts)) <= 1,
		"verdicts": verdicts,
		"tokens": sum(run.get("tokens", 0) for run in runs),
		"wall_time_ms": sum(run.get("wall_time_ms", 0) for run in runs),
		"cost_usd": 0.0,
		"cost_note": "Local open-weights model; cost is $0.00.",
	}


def aggregate(case_scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
	"""Calculate aggregate pass rate and false-positive indicators."""
	scores = list(case_scores.values())
	return {
		"cases": len(scores),
		"passed_cases": sum(score["passed"] for score in scores),
		"pass_rate": sum(score["passed"] for score in scores) / len(scores) if scores else 0.0,
		"false_positive_clean_case_001": not case_scores.get("001_normal_award", {}).get("findings"),
		"false_positive_insufficient_case_007": not case_scores.get("007_insufficient_evidence", {}).get("findings"),
	}
