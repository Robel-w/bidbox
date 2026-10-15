"""Independent evidence review and borrowed-source verification."""

import json
from pathlib import PurePosixPath
from pathlib import Path

from audit.logger import append_audit_event

from .investigator import call_tool, load_tools
from .llm import ask_json


ACCUSATORY = ("fraud", "corrupt", "bribe", "collusion", "criminal", "winner", "award to")
_FIXTURE_RECORDS: list[dict] | None = None


def _cited_value(record: dict, field_path: str):
	"""Resolve the small OCDS field-path subset emitted by BidBox tools."""
	if field_path == "awards[0].value.amount":
		return record.get("awards", [{}])[0].get("value", {}).get("amount")
	if field_path == "awards[0].suppliers":
		return record.get("awards", [{}])[0].get("suppliers")
	return None


async def review_evidence(state: dict) -> dict:
	"""Apply deterministic sourcing checks, filesystem verification, and an LLM pass."""
	global _FIXTURE_RECORDS
	tools = await load_tools()
	kept: list[dict] = []
	dropped: list[dict] = []
	limitations = list(state.get("limitations", []))
	for finding in state.get("draft_findings", []):
		if not finding.get("sources"):
			dropped.append({"finding": finding, "reason": "sources are empty"})
			continue
		if any(word in finding.get("message", "").lower() for word in ACCUSATORY):
			dropped.append({"finding": finding, "reason": "accusatory or award wording"})
			continue
		verified = True
		for source in finding["sources"]:
			try:
				if PurePosixPath(source["source_url"]).name != "releases.json":
					raise ValueError("source is outside the allowed fixture")
				reader = "read_text_file" if "read_text_file" in tools else "read_file"
				if _FIXTURE_RECORDS is None:
					raw = await call_tool(
						tools,
						state["run_id"],
						reader,
						{"path": str(Path("data/fixtures/releases.json").resolve())},
					)
					_FIXTURE_RECORDS = raw if isinstance(raw, list) else json.loads(raw)
				records = _FIXTURE_RECORDS
				match = next((item for item in records if item.get("ocid") == source["ocid"]), None)
				if not match:
					raise ValueError("cited OCID not found in fixture")
				if "claim_value" in finding:
					cited_value = _cited_value(match, source["field_path"])
					if cited_value != finding["claim_value"]:
						raise ValueError(
							f"cited field value {cited_value!r} does not match claim {finding['claim_value']!r}"
						)
			except Exception as exc:
				verified = False
				limitations.append(f"Source verification skipped or failed: {exc}")
				append_audit_event(state["run_id"], "agent", "source_verification", {"source": source}, {"verified": False, "reason": str(exc)})
				break
		if verified:
			kept.append(finding)

	response = await ask_json(
		state["run_id"],
		"Review evidence independently. Return JSON {kept_codes: string[], drops: [{code,reason}]}. Keep only sourced, objective observations; remove overclaiming.",
		json.dumps({"findings": kept, "limitations": limitations}),
		{"kept_codes": [], "drops": []},
	)
	allowed_codes = set(response.get("kept_codes", []))
	reviewed = [finding for finding in kept if finding["code"] in allowed_codes]
	for item in response.get("drops", []):
		dropped.append({"finding": item.get("code"), "reason": item.get("reason", "LLM review")})
	if not reviewed and not dropped and state.get("tool_outputs"):
		limitations.append("Independent review retained no supported finding.")
	return {"findings": reviewed, "dropped_findings": dropped, "limitations": limitations, "insufficient_evidence": not bool(reviewed)}
