"""Read-only committee review API with one delegated approval mutation."""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlsplit
from functools import lru_cache
from pathlib import Path
import re
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from bidbox.data import data_status, get_process, search_processes
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from audit.events import record_approval
from audit.metrics import build_run_metrics
from audit.logger import verify_chain


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PUBLIC_DIR = Path(__file__).resolve().parents[1] / "apps" / "web" / "public"
app = FastAPI(title="BidBox Committee Review", version="0.1.0")
_APPROVAL_LOCK = Lock()


def reports_root() -> Path:
    return Path("reports").resolve()


def safe_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return run_id


def run_paths(run_id: str) -> tuple[Path, Path]:
    safe_run_id(run_id)
    root = reports_root()
    report = (root / "examples" / run_id / "investigation_report.json").resolve()
    audit = (root / "audit" / f"{run_id}.jsonl").resolve()
    if root not in report.parents or root not in audit.parents:
        raise HTTPException(status_code=404, detail="Run not found")
    return report, audit


def read_events(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    events: list[dict] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def read_report(report_path: Path) -> dict:
    if not report_path.exists():
        return {}
    return json.loads(report_path.read_text(encoding="utf-8"))


def approval_event(events: list[dict]) -> dict | None:
    return next((event for event in reversed(events) if event.get("tool") == "approval_gate"), None)


def has_pending(events: list[dict]) -> bool:
    last_request = max((e.get("seq", -1) for e in events if e.get("tool") == "request_approval"), default=None)
    if last_request is None:
        return False
    approval = approval_event(events)
    return approval is None or approval.get("seq", -1) < last_request


def run_status(report: dict, events: list[dict]) -> str:
    if has_pending(events):
        return "awaiting_approval"
    approval = approval_event(events)
    if approval:
        return "approved" if approval.get("inputs", {}).get("decision") == "approve" else "rejected"
    return "done" if report else "running"


@lru_cache(maxsize=1)
def fixture_records() -> dict[str, dict]:
    path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "releases.json"
    return {record["ocid"]: record for record in json.loads(path.read_text(encoding="utf-8")) if record.get("synthetic")}


def review_context(report: dict, events: list[dict]) -> dict:
    review = next((event.get("outputs") for event in reversed(events)
                   if event.get("tool") == "evidence_review" and isinstance(event.get("outputs"), dict)), {})
    contract = review.get("contract") or next((event["outputs"] for event in events
               if event.get("tool") == "inspect_tender" and isinstance(event.get("outputs"), dict)
               and event["outputs"].get("title") and event["outputs"].get("ocid")), {})
    ocid = contract.get("ocid") or next((finding.get("ocid") for finding in report.get("findings", []) if finding.get("ocid")), None)
    if not ocid:
        for event in events:
            if event.get("tool") not in ("inspect_tender", "flag_finding", "benchmark_award"):
                continue
            inputs = event.get("inputs") or {}
            ocid = inputs.get("ocid") or inputs.get("args_summary", {}).get("ocid") or inputs.get("kwargs", {}).get("ocid")
            if ocid:
                break
    context_source = "recorded" if contract else "not recorded"
    if not contract and ocid in fixture_records():
        record = fixture_records()[ocid]
        award = record.get("awards", [{}])[0]
        contract = {"ocid": ocid, "title": award.get("title"), "buyer_name": record.get("buyer", {}).get("name"),
                    "amount": award.get("value", {}).get("amount"), "currency": award.get("value", {}).get("currency"),
                    "supplier_name": next((supplier.get("name") for supplier in award.get("suppliers", [])), None),
                    "origin": "synthetic", "sources": [{"ocid": ocid, "field_path": "awards[0]", "source_url": "data/fixtures/releases.json"}]}
        context_source = "current fixture"
    origins = {event.get("origin") for event in events if event.get("origin")}
    if contract.get("origin"):
        origins.add(contract["origin"])
    origin = "mixed" if "mixed" in origins or {"synthetic", "live-portal"} <= origins else (
        "synthetic" if "synthetic" in origins else "live-portal" if "live-portal" in origins else "not recorded")
    request = next((event for event in reversed(events) if event.get("tool") == "request_approval"), None)
    request_inputs = (request or {}).get("inputs") or {}
    return {"ocid": ocid, "contract": contract, "context_source": context_source, "origin": origin,
            "review": review or report.get("review_summary") or {}, "request": request,
            "requester_name": request_inputs.get("requester_name"), "requester_role": request_inputs.get("requester_role")}


def run_summary(run_id: str, report: dict, events: list[dict]) -> dict:
    verified, _ = verify_chain(reports_root() / "audit" / f"{run_id}.jsonl")
    approval = approval_event(events)
    started = events[0].get("ts_utc") if events else report.get("generated_at_utc")
    context = review_context(report, events)
    return {
        "run_id": run_id,
        "ocid": context["ocid"],
        "title": context["contract"].get("title"),
        "buyer_name": context["contract"].get("buyer_name"),
        "origin": context["origin"],
        "report_available": bool(report),
        "file_created": bool(report) and not has_pending(events) and (not approval or approval.get("inputs", {}).get("decision") == "approve")
                        and any(event.get("tool") == "create_investigation_file" and event.get("status") == "ok"
                                and event.get("seq", -1) > (approval or {}).get("seq", -1) for event in events),
        "started_at": started,
        "status": run_status(report, events),
        "chain_verified": verified,
        "approval": approval,
    }


def all_runs() -> list[dict]:
    root = reports_root()
    result = []
    for audit_path in root.glob("audit/*.jsonl"):
        run_id = audit_path.stem
        if not RUN_ID_PATTERN.fullmatch(run_id):
            continue
        report_path = root / "examples" / run_id / "investigation_report.json"
        result.append(run_summary(run_id, read_report(report_path), read_events(audit_path)))
    return sorted(result, key=lambda item: item.get("started_at") or "", reverse=True)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    request_seq: int = Field(ge=0, strict=True)
    independent_reviewer: Literal[True]
    approver_name: str = Field(min_length=1, max_length=120)
    approver_role: str = Field(min_length=1, max_length=120)
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=1000)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/capabilities")
def capabilities() -> dict:
    host = urlsplit(os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")).hostname
    configured = bool(os.getenv("LLM_API_KEY")) or host in ("localhost", "127.0.0.1", "::1")
    return {"can_start_review": False, "authenticated_approval": False, "integrations": [
        {"name": "Model provider", "status": "configured" if configured else "not configured",
         "detail": "Host configuration only; provider availability is checked when an investigation starts."},
        {"name": "Kenya PPIP OCDS", "status": "available" if data_status()["counts"]["live-portal"] else "not configured", "detail": "Downloaded publisher records with fiscal-year membership and source timestamps. Download completion is not verified full-year coverage."},
        {"name": "Local OCDS fixtures", "status": "sandbox", "detail": "Separate synthetic test fixtures. Never substituted for live search results."},
        {"name": "Hash-chained audit", "status": "available", "detail": "Recorded actions, sources and named committee decisions."},
        {"name": "Free-text investigation startup", "status": "planned", "detail": "This viewer opens recorded reviews; new investigations use the runner."},
        {"name": "Approval invitations & verified identity", "status": "planned", "detail": "No SMS/email delivery, signed invitations or authenticated identity yet."},
    ]}


@app.get("/api/data/status")
def publisher_data_status() -> dict:
    return data_status()


@app.get("/api/data/search")
def publisher_search(q: str = Query(default="", max_length=240), limit: int = Query(default=20, ge=1, le=50)) -> dict:
    return search_processes(q, limit)


@app.get("/api/data/process")
def publisher_process(ocid: str = Query(min_length=1, max_length=300)) -> dict:
    process = get_process(ocid)
    if process is None:
        raise HTTPException(status_code=404, detail="Contract process not found in live publisher data; no sandbox fallback.")
    return process


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return all_runs()


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    report_path, audit_path = run_paths(run_id)
    if not audit_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    report = read_report(report_path)
    events = read_events(audit_path)
    metrics = build_run_metrics(run_id)
    verified, error = verify_chain(audit_path)
    context = review_context(report, events)
    pending = has_pending(events)
    review = context["review"]
    findings = review.get("findings", []) if pending or not report else report.get("findings", [])
    recovered_limits = [limit for event in events if event.get("tool") == "recovery" and isinstance(event.get("outputs"), dict)
                        for limit in event["outputs"].get("limitations", [])]
    limitations = list(dict.fromkeys([*report.get("data_limitations", []), *review.get("limitations", []), *recovered_limits]))
    timeouts = []
    for event in events:
        if event.get("actor") == "agent" and event.get("status") == "timeout":
            recovered = any(item.get("tool") == event.get("tool") and item.get("status") == "ok"
                            and item.get("seq", -1) > event.get("seq", -1) for item in events)
            timeouts.append({"tool": event.get("tool"), "recovered": recovered})
            if not recovered:
                limitations.append("Couldn’t reach the data source. Continuing with what we have. Check the tool log for the timed-out request.")
    if review.get("status") == "failed":
        limitations.append("The automated evidence review did not pass validation. Manual verification is needed before relying on these results.")
    return {
        **run_summary(run_id, report, events),
        **context,
        "events": events,
        "findings": findings,
        "dropped_findings": review.get("drops", report.get("dropped_findings", [])),
        "limitations": list(dict.fromkeys(limitations)),
        "timeouts": timeouts,
        "evidence_missing": bool(pending and (not review or review.get("retained_count", 0) > len(findings))),
        "tokens": (metrics["tokens"]["input_total"] + metrics["tokens"]["output_total"]
                   if metrics["tokens"]["input_total"] is not None and metrics["tokens"]["output_total"] is not None else None),
        "model": metrics["model_name"],
        "metrics": metrics,
        "report_available": report_path.exists(),
        "cost_per_run": metrics["list_price_cost_usd"],
        "cost_note": "; ".join(metrics["notes"]),
        "report_path": str(report_path),
        "chain_error": error,
    }


@app.get("/api/runs/{run_id}/source")
def fixture_source(run_id: str, ocid: str) -> dict:
    detail = run_detail(run_id)
    referenced = {detail.get("ocid")}
    for finding in detail["findings"]:
        referenced.update(source.get("ocid") for source in finding.get("sources", []))
    if ocid not in referenced or ocid not in fixture_records():
        raise HTTPException(status_code=404, detail="Recorded fixture source not found")
    return {"origin": "synthetic", "notice": "Current local fixture, not an archived publisher snapshot.", "record": fixture_records()[ocid]}


@app.get("/api/runs/{run_id}/events")
async def event_stream(run_id: str) -> StreamingResponse:
    _, audit_path = run_paths(run_id)
    if not audit_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    async def stream():
        position = 0
        idle = 0
        while idle < 450:
            lines = audit_path.read_text(encoding="utf-8").splitlines()
            fresh = lines[position:]
            position = len(lines)
            if fresh:
                idle = 0
                for line in fresh:
                    if line.strip():
                        yield f"data: {line}\n\n"
            else:
                idle += 1
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}/report")
def report_markdown(run_id: str) -> FileResponse:
    report_path, _ = run_paths(run_id)
    markdown_path = (report_path.parent / "investigation_report.md").resolve()
    if reports_root() not in markdown_path.parents or not markdown_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(markdown_path, media_type="text/markdown")


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, request: ApprovalRequest) -> dict:
    _, audit_path = run_paths(run_id)
    if not audit_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    with _APPROVAL_LOCK:
        events = read_events(audit_path)
        if not has_pending(events):
            raise HTTPException(status_code=409, detail="Run has no pending approval")
        verified, _ = verify_chain(audit_path)
        if not verified:
            raise HTTPException(status_code=409, detail="The audit trail could not be verified. Ask the requester to check it before recording a decision.")
        context = review_context({}, events)
        if context["request"]["seq"] != request.request_seq:
            raise HTTPException(status_code=409, detail="This approval request has changed. Read the current evidence before making a decision.")
        requester = context.get("requester_name")
        if requester and requester.strip().casefold() == request.approver_name.casefold():
            raise HTTPException(status_code=422, detail="The approver must be a different person from the requester.")
        review = context["review"]
        if request.decision == "approve" and (not review or review.get("retained_count", 0) > len(review.get("findings", []))):
            raise HTTPException(status_code=409, detail="The full reviewed evidence was not saved. Ask the requester for it before approving.")
        action = context["request"].get("inputs", {}).get("action", "committee_review")
        return record_approval(run_id, action, request.approver_name, request.approver_role, request.decision,
                               request.reason, request_seq=request.request_seq, independent_reviewer=True)


app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")
