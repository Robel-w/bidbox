"""Read-only committee review API with one delegated approval mutation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from audit.events import get_run_cost, record_approval
from audit.logger import verify_chain


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PUBLIC_DIR = Path(__file__).resolve().parents[1] / "apps" / "web" / "public"
app = FastAPI(title="BidBox Committee Review", version="0.1.0")


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
    pending = any(event.get("tool") == "request_approval" for event in events)
    return pending and approval_event(events) is None


def run_status(report: dict, events: list[dict]) -> str:
    approval = approval_event(events)
    if approval:
        return "approved" if approval.get("inputs", {}).get("decision") == "approve" else "rejected"
    if has_pending(events):
        return "awaiting_approval"
    return "done" if report else "running"


def run_summary(run_id: str, report: dict, events: list[dict]) -> dict:
    verified, _ = verify_chain(reports_root() / "audit" / f"{run_id}.jsonl")
    approval = approval_event(events)
    started = events[0].get("ts_utc") if events else report.get("generated_at_utc")
    return {
        "run_id": run_id,
        "ocid": next((event.get("inputs", {}).get("kwargs", {}).get("ocid") for event in events if event.get("tool") == "flag_finding"), "not recorded"),
        "started_at": started,
        "status": run_status(report, events),
        "chain_verified": verified,
        "approval": approval,
    }


def all_runs() -> list[dict]:
    root = reports_root()
    result = []
    for report_path in root.glob("examples/*/investigation_report.json"):
        run_id = report_path.parent.name
        audit_path = root / "audit" / f"{run_id}.jsonl"
        if audit_path.exists():
            result.append(run_summary(run_id, read_report(report_path), read_events(audit_path)))
    return sorted(result, key=lambda item: item.get("started_at") or "", reverse=True)


class ApprovalRequest(BaseModel):
    approver_name: str = Field(min_length=1)
    approver_role: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return all_runs()


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    report_path, audit_path = run_paths(run_id)
    if not report_path.exists() or not audit_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    report = read_report(report_path)
    events = read_events(audit_path)
    cost = get_run_cost(run_id)
    verified, error = verify_chain(audit_path)
    return {
        **run_summary(run_id, report, events),
        "events": events,
        "findings": report.get("findings", []),
        "dropped_findings": report.get("dropped_findings", []),
        "limitations": report.get("data_limitations", []),
        "tokens": cost.total_tokens,
        "cost_per_run": cost.cost_per_run,
        "cost_note": cost.note,
        "report_path": str(report_path),
        "chain_error": error,
    }


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
    events = read_events(audit_path)
    if not has_pending(events):
        raise HTTPException(status_code=409, detail="Run has no pending approval")
    action = next(event.get("inputs", {}).get("action", "committee_review") for event in events if event.get("tool") == "request_approval")
    event = record_approval(run_id, action, request.approver_name, request.approver_role, request.decision, request.reason)
    return event


app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")
