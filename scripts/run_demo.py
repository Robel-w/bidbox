"""Run the Phase 4 investigation graph from the command line."""

import argparse
import asyncio
from pathlib import Path
import sys
import uuid

from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.graph import build_graph
from audit.logger import get_audit_logs, verify_chain


async def run(ocid: str, approver: str | None, role: str) -> None:
	run_id = f"demo-{uuid.uuid4().hex[:8]}"
	graph = build_graph()
	config = {"configurable": {"thread_id": run_id}}
	result = await graph.ainvoke({"run_id": run_id, "ocid": ocid, "step_budget": 6, "steps_used": 0, "limitations": []}, config)
	while "__interrupt__" in result:
		prompt = result["__interrupt__"][0].value
		name = approver or input(f"Approval required for {prompt['action']}. Approver name: ").strip()
		result = await graph.ainvoke(Command(resume={"approver_name": name, "approver_role": role, "reason": "Reviewed for committee"}), config)
	report = result.get("report", {})
	path = f"reports/audit/{run_id}.jsonl"
	print(f"report: {report.get('markdown_path', 'not generated')}")
	print(f"audit: {verify_chain(path)}; events={len(get_audit_logs(run_id))}")


def main() -> None:
	parser = argparse.ArgumentParser(description="Run the BidBox Phase 4 investigation demo.")
	parser.add_argument("--ocid", required=True)
	parser.add_argument("--approver")
	parser.add_argument("--role", default="Procurement Review Officer")
	args = parser.parse_args()
	asyncio.run(run(args.ocid, args.approver, args.role))


if __name__ == "__main__":
	main()
