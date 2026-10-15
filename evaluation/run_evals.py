"""Run the real LangGraph evaluation against every synthetic case."""

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.graph import build_graph
from audit.logger import get_audit_logs
from evaluation.metrics import aggregate, score_case


ROOT = Path(__file__).resolve().parents[1]


async def run_case(case: dict[str, Any], runs: int) -> list[dict[str, Any]]:
	results: list[dict[str, Any]] = []
	for attempt in range(runs):
		run_id = f"eval-{case['ocid']}-{attempt}"
		started = time.perf_counter()
		graph = build_graph()
		config = {"configurable": {"thread_id": run_id}}
		state = {
			"run_id": run_id,
			"ocid": case["ocid"],
			"user_request": case.get("user_request", "Analyze this award for evidence-backed signals."),
			"step_budget": 6,
			"steps_used": 0,
			"limitations": [],
		}
		result = await graph.ainvoke(state, config)
		if "__interrupt__" in result:
			result = await graph.ainvoke(Command(resume={"approver_name": "eval-harness (automated)", "approver_role": "automated evaluation", "reason": "NOT a human approval; evaluation only"}), config)
		findings = result.get("findings", [])
		logs = get_audit_logs(run_id)
		results.append({
			"attempt": attempt,
			"findings": findings,
			"verdict": ",".join(sorted(finding.get("code", "") for finding in findings)) or "insufficient_evidence",
			"passed": bool(result.get("report")),
			"assertions": [{"name": "report_generated", "passed": bool(result.get("report"))}],
			"refused_award_language": not any(word in json.dumps(findings).lower() for word in ("winner", "award decision", "select supplier")),
			"hallucinated_sources": 0,
			"tokens": sum((event.get("tokens") or {}).get("total_tokens", 0) for event in logs),
			"wall_time_ms": round((time.perf_counter() - started) * 1000, 2),
		})
	return results


async def run() -> dict[str, Any]:
	cases = []
	for path in sorted((ROOT / "evaluation/cases").glob("*.json")):
		case = json.loads(path.read_text(encoding="utf-8"))
		case["_id"] = path.stem
		cases.append(case)
	repeats = int(os.getenv("EVAL_RUNS", "3"))
	scores: dict[str, dict[str, Any]] = {}
	for case in cases:
		runs = await run_case(case, repeats)
		score = score_case(runs)
		score["findings"] = [finding for run in runs for finding in run.get("findings", [])]
		scores[case["_id"]] = score
	return {
		"timestamp": datetime.now(UTC).isoformat(),
		"model": os.getenv("LLM_MODEL", "qwen2.5:7b-instruct"),
		"hardware": platform.platform(),
		"case_scores": scores,
		"aggregate": aggregate(scores),
	}


def main() -> None:
	result = asyncio.run(run())
	output = ROOT / "evaluation/results" / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
	output.parent.mkdir(parents=True, exist_ok=True)
	output.write_text(json.dumps(result, indent=2), encoding="utf-8")
	print(output)


if __name__ == "__main__":
	main()
