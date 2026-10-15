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
import argparse

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


def source_exists(source: dict[str, Any]) -> bool:
	fixture = ROOT / "data" / "fixtures" / "releases.json"
	if Path(source.get("source_url", "")).name != fixture.name or not fixture.exists():
		return False
	records = json.loads(fixture.read_text(encoding="utf-8"))
	return any(record.get("ocid") == source.get("ocid") for record in records)


def _result_path(resume: bool) -> Path:
	result_dir = ROOT / "evaluation" / "results"
	if resume:
		files = sorted(result_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
		if not files:
			raise SystemExit("--resume requested but evaluation/results contains no run file")
		return files[0]
	return result_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"


def write_result(path: Path, result: dict[str, Any]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(result, indent=2), encoding="utf-8")


async def run(output: Path, selected: set[str] | None = None, resume: bool = False) -> dict[str, Any]:
	cases = []
	for path in sorted((ROOT / "evaluation/cases").glob("*.json")):
		case = json.loads(path.read_text(encoding="utf-8"))
		case["_id"] = path.stem
		cases.append(case)
	repeats = int(os.getenv("EVAL_RUNS", "2"))
	timeout_seconds = float(os.getenv("EVAL_CASE_TIMEOUT", "240"))
	result: dict[str, Any] = {"timestamp": datetime.now(UTC).isoformat(), "model": os.getenv("LLM_MODEL", "qwen2.5:7b-instruct"), "hardware": platform.platform(), "case_scores": {}, "aggregate": {}}
	if resume and output.exists():
		result = json.loads(output.read_text(encoding="utf-8"))
	for case in cases:
		if selected and case["_id"] not in selected or case["_id"] in result["case_scores"]:
			continue
		case_started = time.perf_counter()
		try:
			runs = await asyncio.wait_for(run_case(case, repeats), timeout=timeout_seconds)
			for run in runs:
				run["hallucinated_sources"] = 0
				for finding in run.get("findings", []):
					hallucinated = any(not source_exists(source) for source in finding.get("sources", []))
					finding["hallucinated_source"] = hallucinated
					run["hallucinated_sources"] += int(hallucinated)
			score = score_case(runs)
			score["findings"] = [finding for run in runs for finding in run.get("findings", [])]
		except BaseException as exc:
			if not isinstance(exc, asyncio.TimeoutError) and time.perf_counter() - case_started < timeout_seconds:
				raise
			score = {"outcome": "timeout", "timed_out": True, "runs": repeats, "completed_runs": 0, "passed": False, "sourcing_rate": 0.0, "hallucinated_source_rate": 0.0, "agreement": False, "findings": []}
		result["case_scores"][case["_id"]] = score
		result["aggregate"] = aggregate(result["case_scores"])
		write_result(output, result)
	return result


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--resume", action="store_true")
	parser.add_argument("--cases", help="Comma-separated case IDs, for example 001_normal_award,002_price_outlier")
	args = parser.parse_args()
	output = _result_path(args.resume)
	selected = {item.strip() for item in args.cases.split(",")} if args.cases else None
	result = asyncio.run(run(output, selected, args.resume))
	aggregate_result = result.get("aggregate", {})
	print(f"result={output}")
	print(f"pass_rate={aggregate_result.get('pass_rate', 0):.3f} sourcing_rate={aggregate_result.get('sourcing_rate', 0):.3f} hallucinated_source_rate={aggregate_result.get('hallucinated_source_rate', 0):.3f} agreement={aggregate_result.get('run_to_run_agreement', 0):.3f} timed_out={aggregate_result.get('timed_out_cases', 0)}")


if __name__ == "__main__":
	main()
