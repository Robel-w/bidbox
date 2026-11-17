"""Independent evidence review and borrowed-source verification."""

import json
import re

from bidbox.data import get_process
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from audit.logger import append_audit_event

from .investigator import call_tool, load_tools
from .llm import ask_json


ACCUSATORY = ("fraud", "corrupt", "bribe", "collusion", "criminal", "winner", "award to")
_FIXTURE_RECORDS: list[dict] | None = None
ReviewText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, strict=True)]


class ReviewDrop(BaseModel):
	model_config = ConfigDict(extra="forbid", strict=True)
	code: ReviewText
	reason: ReviewText


class ReviewResponse(BaseModel):
	model_config = ConfigDict(extra="forbid", strict=True)
	kept_codes: list[ReviewText]
	drops: list[ReviewDrop]
	reason: ReviewText


def _cited_value(record: dict, field_path: str):
	"""Resolve the small OCDS field-path subset emitted by BidBox tools."""
	match = re.fullmatch(r"awards\[(\d+)\]\.(value.amount|suppliers)", field_path)
	if not match:
		return None
	awards = record.get("awards") or []
	position = int(match[1])
	if position >= len(awards):
		return None
	award = awards[position]
	return (award.get("value") or {}).get("amount") if match[2] == "value.amount" else award.get("suppliers")


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
				process = get_process(source["ocid"])
				if process:
					if source["source_url"] != process["source_url"]:
						raise ValueError("citation URL differs from the downloaded release provenance")
					match = process["record"]
				else:
					allowed = {"data/fixtures/releases.json", str(Path("data/fixtures/releases.json").resolve())}
					if source["source_url"] not in allowed:
						raise ValueError("cited live OCID not found; no sandbox fallback")
					reader = "read_text_file" if "read_text_file" in tools else "read_file"
					if _FIXTURE_RECORDS is None:
						raw = await call_tool(tools, state["run_id"], reader, {"path": str(Path("data/fixtures/releases.json").resolve())})
						_FIXTURE_RECORDS = raw if isinstance(raw, list) else json.loads(raw)
					match = next((item for item in _FIXTURE_RECORDS if item.get("ocid") == source["ocid"]), None)
					if not match:
						raise ValueError("cited OCID not found in explicit sandbox fixture")
				award_ids = {str(award.get("id")) for award in match.get("awards") or []}
				if source.get("award_id") not in award_ids:
					raise ValueError("cited award ID not found in the source process")
				position = re.match(r"awards\[(\d+)\](?:\.|$)", source["field_path"])
				if not position or int(position[1]) >= len(match.get("awards") or []) or str(match["awards"][int(position[1])].get("id")) != source["award_id"]:
					raise ValueError("cited award field position does not match award ID")
				if "claim_value" in finding:
					cited_value = _cited_value(match, source["field_path"])
					if cited_value != finding["claim_value"]:
						raise ValueError(
							f"cited field value {cited_value!r} does not match claim {finding['claim_value']!r}"
						)
				if process:
					append_audit_event(state["run_id"], "agent", "source_verification", {"source": source}, {"verified": True, "basis": "archived downloaded release", "ingested_at": process["ingested_at"]}, origin="live")
			except Exception as exc:
				verified = False
				limitations.append(f"Source verification skipped or failed: {exc}")
				append_audit_event(state["run_id"], "agent", "source_verification", {"source": source}, {"verified": False, "reason": str(exc)})
				break
		if verified:
			kept.append(finding)
		else:
			dropped.append({"finding": finding, "reason": limitations[-1]})

	status = "ok"
	try:
		response = await ask_json(
			state["run_id"],
			'Review evidence independently. Return only JSON with exactly these keys: '
			'{"kept_codes": ["CODE"], "drops": [{"code": "CODE", "reason": "why dropped"}], "reason": "one-line review conclusion"}. '
			'Use only codes present in findings. Account for EVERY input code exactly once, either kept or dropped. '
			'Keep sourced, objective observations; drop overclaiming with a non-empty reason. '
			'An empty finding list requires empty kept_codes and drops and an explanation in reason.',
			json.dumps({"findings": kept, "limitations": limitations}),
			ReviewResponse.model_json_schema(),
		)
		decision = ReviewResponse.model_validate(response)
		decided_codes = decision.kept_codes + [item.code for item in decision.drops]
		if len(decided_codes) != len(set(decided_codes)) or set(decided_codes) != {item["code"] for item in kept}:
			raise ValueError("Each candidate code must be kept or dropped exactly once; unknown codes are not allowed")
		reason = decision.reason
		reviewed = [finding for finding in kept if finding["code"] in decision.kept_codes]
		dropped.extend({"finding": item.code, "reason": item.reason} for item in decision.drops)
	except Exception as exc:
		status = "failed"
		detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()) if isinstance(exc, ValidationError) else str(exc)
		reason = f"Reviewer output failed validation: {detail[:300]}"
		reviewed = []
		dropped.extend({"finding": item["code"], "reason": reason} for item in kept)
		limitations.append(reason + ". Manual evidence review is required.")

	summary = {
		"candidate_count": len(state.get("draft_findings", [])),
		"verified_count": len(kept),
		"retained_count": len(reviewed),
		"status": status,
		"reason": reason,
		"drops": dropped,
	}
	append_audit_event(state["run_id"], "agent", "evidence_review", {}, {**summary, "findings": reviewed, "contract": state.get("tender"), "limitations": limitations}, status="error" if status == "failed" else "ok")
	return {"findings": reviewed, "dropped_findings": dropped, "review_summary": summary, "limitations": limitations, "insufficient_evidence": not bool(reviewed)}
