"""Run the Phase 4 investigation graph from the command line."""

import argparse
import asyncio
import time
from pathlib import Path
import sys
import uuid

from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.graph import build_graph
from audit.logger import get_audit_logs, verify_chain


async def run(ocid: str, approver: str | None, role: str, approval_mode: str = "cli", approval_timeout: int = 900) -> None:
	run_id = f"demo-{uuid.uuid4().hex[:8]}"
	graph = build_graph()
	config = {"configurable": {"thread_id": run_id}}
	result = await graph.ainvoke({"run_id": run_id, "ocid": ocid, "step_budget": 6, "steps_used": 0, "limitations": []}, config)
	while "__interrupt__" in result:
		prompt = result["__interrupt__"][0].value
		if approval_mode == "web":
			print(f"Waiting for committee approval at http://127.0.0.1:8000/runs/{run_id}", flush=True)
			deadline = time.monotonic() + approval_timeout
			approval = None
			while time.monotonic() < deadline:
				approval = next((event for event in get_audit_logs(run_id) if event.get("tool") == "approval_gate"), None)
				if approval:
					break
				await asyncio.sleep(2)
			if not approval:
				raise TimeoutError(f"No web approval received within {approval_timeout} seconds")
			inputs = approval.get("inputs", {})
			result = await graph.ainvoke(Command(resume={"approver_name": inputs.get("approver_name"), "approver_role": inputs.get("approver_role"), "reason": inputs.get("reason", "")}), config)
		else:
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
	parser.add_argument("--approval", choices=["cli", "web"], default="cli")
	parser.add_argument("--approval-timeout", type=int, default=900)
	args = parser.parse_args()
	asyncio.run(run(args.ocid, args.approver, args.role, args.approval, args.approval_timeout))


if __name__ == "__main__":
	main()
