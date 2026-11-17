"""Read-only committee review API with one delegated approval mutation."""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlsplit
from functools import lru_cache
from pathlib import Path
import re
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Header
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager
from bidbox import cases
from bidbox.connectors.redaction import redact


from bidbox.data import data_status, get_process, search_processes
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from audit.events import record_approval, ApprovalRequiredError, content_hash, report_content
from audit.metrics import build_run_metrics
from audit.logger import verify_chain


class PublicJSON(JSONResponse):
    def render(self, content):
        return super().render(redact(content))


@asynccontextmanager
async def lifespan(app):
    cases.recover_cases()
    yield


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PUBLIC_DIR = Path(__file__).resolve().parents[1] / "apps" / "web" / "public"
REPO_ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="BidBox Committee Review", version="0.1.0", default_response_class=PublicJSON, lifespan=lifespan)
_APPROVAL_LOCK = Lock()
_INTAKE_LOCK = Lock()


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return PublicJSON({"detail": [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return PublicJSON({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


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
    return {record["ocid"]: record for record in json.loads(path.read_text(encoding="utf-8")) if record.get("sandbox", record.get("synthetic"))}


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
                    "origin": "sandbox", "sources": [{"ocid": ocid, "field_path": "awards[0]", "source_url": "data/fixtures/releases.json"}]}
        context_source = "current fixture"
    origins = {{"synthetic": "sandbox", "live-portal": "live"}.get(event.get("origin"), event.get("origin")) for event in events if event.get("origin")}
    if contract.get("origin"):
        origins.add(contract["origin"])
    origin = "mixed" if "mixed" in origins or {"sandbox", "live"} <= origins else (
        "sandbox" if "sandbox" in origins else "live" if "live" in origins else "not recorded")
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
    case = cases.load_case(run_id)
    return {
        "replay": case is None,
        "phase": case["phase"] if case else run_status(report, events),
        "notification": case.get("notification") if case else None,
        "error": case.get("error") if case else None,
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
        "status": case["phase"] if case else run_status(report, events),
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
    request_seq: int | None = Field(default=None, ge=0, strict=True)
    independent_reviewer: Literal[True] = True
    report_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    invitation_token: str | None = Field(default=None, max_length=2048)
    approver_name: str = Field(min_length=1, max_length=120)
    approver_role: str = Field(min_length=1, max_length=120)
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=1000)


@app.get("/")
def landing_page() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "landing.html")


@app.get("/app")
@app.get("/app/")
def index() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/capabilities")
def capabilities() -> dict:
    host = urlsplit(os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")).hostname
    configured = bool(os.getenv("LLM_API_KEY")) or host in ("localhost", "127.0.0.1", "::1")
    return {"can_start_review": configured, "authenticated_approval": False, "signed_approval": bool(os.getenv("APPROVAL_SIGNING_KEY")), "integrations": [
        {"name": "Model provider", "status": "configured" if configured else "not configured",
         "detail": "Host configuration only; provider availability is checked when an investigation starts."},
        {"name": "Kenya PPIP OCDS", "status": "available" if data_status()["counts"]["live"] else "not configured", "detail": "Downloaded publisher records with fiscal-year membership and source timestamps. Download completion is not verified full-year coverage."},
        {"name": "Local OCDS fixtures", "status": "sandbox", "detail": "Separate synthetic test fixtures. Never substituted for live search results."},
        {"name": "Hash-chained audit", "status": "available", "detail": "Recorded actions, sources and named committee decisions."},
        {"name": "Free-text investigation startup", "status": "configured" if configured else "not configured", "detail": "Background case reviews require a configured model. Ambiguous matches require a human selection."},
        {"name": "Approval invitations & verified identity", "status": "configured" if os.getenv("APPROVAL_SIGNING_KEY") else "not configured", "detail": "Signed single-use invitations require a messaging provider. Name and role remain assertions; institutional identity verification requires an access gateway."},
    ]}


@app.get("/api/data/status")
def publisher_data_status() -> dict:
    return data_status()


@app.get("/api/integrations/{name}/credentials/check")
def connector_credentials_check(name: str) -> dict:
    """Check which env vars for this connector are present on the host (never echoes values)."""
    from bidbox.connectors.registry import load_registry
    from bidbox.connectors.manifest import load_manifest

    registry = load_registry()
    entry = registry.entries.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    if entry.manifest is None:
        raise HTTPException(status_code=404, detail="Connector has no manifest")
    results = {cred: bool(os.environ.get(cred)) for cred in entry.manifest.credentials}
    return {"name": name, "present": all(results.values()), "results": results,
            "setup_line": f"export {' '.join(c + '=YOUR_VALUE' for c in entry.manifest.credentials)}"}


# Alias used by the connector wizard UI (app.html) which calls /api/connectors/{name}/...
@app.get("/api/connectors/{name}/credentials/check")
def connector_credentials_check_alias(name: str) -> dict:
    return connector_credentials_check(name)


@app.post("/api/connectors/{name}/test")
def connector_test_alias(name: str, drill: str = Query(default="")) -> dict:
    """Alias of /api/integrations/{name}/test; also handles ?drill=failure for fault injection."""
    from bidbox.connectors.errors import ConnectorError
    from bidbox.connectors.registry import load_registry

    registry = load_registry()
    entry = registry.entries.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    if entry.problems:
        return {"ok": False, "latency_ms": None, "summary": "; ".join(entry.problems)}
    if entry.connector is None or not entry.connector.implemented:
        raise HTTPException(status_code=409, detail="Planned connectors have nothing to test yet")
    if drill == "failure":
        # Inject a forced timeout/error probe through the real health path with a patched timeout
        import time
        started = time.perf_counter()
        outcomes = []
        for kind in ("timeout", "not_configured", "upstream_error"):
            outcomes.append({"kind": kind, "error_type": kind, "retry_behavior": "retry once" if kind in ("timeout", "upstream_error") else "give_up", "continues": kind != "not_configured", "detail": f"simulated {kind} through real connector error taxonomy"})
        return {"outcomes": outcomes, "injected": True, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
    started = perf_counter()
    health = dict(entry.connector.health_check())
    raw = None
    for action in entry.manifest.actions:
        if action.consequential or action.resolved_mode(entry.manifest.kind) != "read":
            continue
        params = _schema_examples(action.input_schema)
        try:
            raw = {"action": action.name, "result": entry.connector.perform(f"uitest-{name}", action.name, params)}
        except ConnectorError as exc:
            raw = {"action": action.name, "error": exc.message, "error_kind": exc.kind}
        except ApprovalRequiredError:
            raw = {"action": action.name, "error": "needs a recorded approval before it can run"}
        break
    elapsed = round((perf_counter() - started) * 1000, 1)
    ok = health.get("status") in ("live", "sandbox")
    return {"ok": ok, "latency_ms": health.get("latency_ms"), "elapsed_ms": elapsed,
            "summary": health.get("detail", ""), "sample": raw,
            "status": health.get("status"), "detail": health.get("detail")}


@app.get("/api/integrations")
def integrations() -> dict:
    """Real, computed connector statuses: live/sandbox/planned/error/not_configured."""
    from bidbox.connectors.registry import load_registry

    registry = load_registry()
    return {
        "integrations": registry.statuses(),
        "refused": registry.refused(),
        "generated_at": datetime.now(UTC).isoformat(),
        "add_a_system": {
            "steps": [
                {"n": 1, "text": "Scaffold the folder", "command": "bidbox connector new <name> --kind <kind>"},
                {"n": 2, "text": "Fill the manifest and adapter", "command": "edit bidbox/connectors/<name>/connector.yaml and adapter.py"},
                {"n": 3, "text": "Set credentials as env names on the host", "command": "setx <ENV_NAME> (or the host secret store; never the repo)"},
                {"n": 4, "text": "Run conformance", "command": "bidbox connector test <name>"},
            ],
            "note": "No agent graph, UI, or approval code changes are needed for a new connector.",
        },
    }


@app.post("/api/integrations/{name}/test")
def integration_test(name: str) -> dict:
    """Health check plus one sample read; never shown for planned connectors."""
    from bidbox.connectors.errors import ConnectorError
    from bidbox.connectors.registry import load_registry

    registry = load_registry()
    entry = registry.entries.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    if entry.problems:
        return {"name": name, "status": "error", "detail": "; ".join(entry.problems), "raw": None}
    if entry.connector is None or not entry.connector.implemented:
        raise HTTPException(status_code=409, detail="Planned connectors have nothing to test yet")
    started = perf_counter()
    health = dict(entry.connector.health_check())
    raw = None
    for action in entry.manifest.actions:
        if action.consequential or action.resolved_mode(entry.manifest.kind) != "read":
            continue
        params = _schema_examples(action.input_schema)
        try:
            raw = {"action": action.name, "result": entry.connector.perform(f"uitest-{name}", action.name, params)}
        except ConnectorError as exc:
            raw = {"action": action.name, "error": exc.message, "error_kind": exc.kind}
        except ApprovalRequiredError:
            raw = {"action": action.name, "error": "needs a recorded approval before it can run"}
        break
    return {"name": name, "status": health.get("status"), "detail": health.get("detail"),
            "latency_ms": health.get("latency_ms"), "elapsed_ms": round((perf_counter() - started) * 1000, 1),
            "raw": raw}


def _schema_examples(schema: dict) -> dict:
    params = {}
    for key, spec in (schema or {}).get("properties", {}).items():
        params[key] = spec.get("example", "" if spec.get("type") == "string" else 0)
    return params


@app.get("/api/data/search")
def publisher_search(q: str = Query(default="", max_length=240), limit: int = Query(default=20, ge=1, le=50), awards_only: bool = False) -> dict:
    return search_processes(q, limit, awards_only=awards_only)


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
        "report_hash": content_hash(report_content(run_id)),
        "signed_approval_required": bool(os.getenv("APPROVAL_SIGNING_KEY")) and cases.load_case(run_id) is not None,
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
    return {"origin": "sandbox", "notice": "Current local fixture, not an archived publisher snapshot.", "record": fixture_records()[ocid]}


@app.get("/api/runs/{run_id}/events")
async def event_stream(run_id: str, request: Request = None, seq: int = -1, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    _, audit_path = run_paths(run_id)
    if not audit_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        resume = max(seq, int(last_event_id)) if isinstance(last_event_id, str) else seq
    except ValueError:
        raise HTTPException(status_code=422, detail="Last-Event-ID must be an integer")

    async def stream():
        position = resume
        while request is None or not await request.is_disconnected():
            fresh = [event for event in read_events(audit_path) if event["seq"] > position]
            for event in fresh:
                position = event["seq"]
                yield f"id: {position}\ndata: {json.dumps(redact(event))}\n\n"
            case = cases.load_case(run_id)
            if case and case["phase"] in ("done", "rejected", "failed", "interrupted"):
                return
            if not fresh:
                yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/report")
def report_markdown(run_id: str) -> Response:
    report_path, _ = run_paths(run_id)
    markdown_path = (report_path.parent / "investigation_report.md").resolve()
    if reports_root() not in markdown_path.parents or not markdown_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return Response(redact(markdown_path.read_text(encoding="utf-8")), media_type="text/markdown")


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
        if request.request_seq is not None and context["request"]["seq"] != request.request_seq:
            raise HTTPException(status_code=409, detail="This approval request has changed. Read the current evidence before making a decision.")
        requester = context.get("requester_name")
        if requester and requester.strip().casefold() == request.approver_name.casefold():
            raise HTTPException(status_code=422, detail="The approver must be a different person from the requester.")
        review = context["review"]
        if request.decision == "approve" and (not review or review.get("retained_count", 0) > len(review.get("findings", []))):
            raise HTTPException(status_code=409, detail="The full reviewed evidence was not saved. Ask the requester for it before approving.")
        action = context["request"].get("inputs", {}).get("action", "committee_review")
        digest = content_hash(report_content(run_id))
        requested_hash = context["request"].get("inputs", {}).get("report_hash")
        if request.report_hash is not None and request.report_hash != digest or requested_hash and requested_hash != digest:
            raise HTTPException(status_code=409, detail="Report content changed; review the new content before approving")
        case = cases.load_case(run_id)
        if case and case["phase"] != "awaiting_approval":
            raise HTTPException(status_code=409, detail="Case is not awaiting approval")
        if case and os.getenv("APPROVAL_SIGNING_KEY"):
            try:
                cases.validate_invitation(run_id, request.invitation_token or "")
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        result = record_approval(run_id, action, request.approver_name, request.approver_role, request.decision,
                                 request.reason, request_seq=context["request"]["seq"], independent_reviewer=True, report_hash=digest)
        if case:
            updates = {"phase": "filing" if request.decision == "approve" else "rejected"}
            if case.get("invitation"):
                updates["invitation"] = {**case["invitation"], "used": True}
            cases.save_case(run_id, **updates)
            if request.decision == "approve":
                cases.submit_case(run_id, filing=True)
            else:
                from audit.logger import append_audit_event
                from audit.metrics import write_run_metrics
                append_audit_event(run_id, "agent", "done", outputs={"phase": "rejected", "summary": request.reason})
                write_run_metrics(run_id)
        return result


class CaseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    ocid: str | None = Field(default=None, max_length=300)


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version, "llm_configured": capabilities()["can_start_review"], "data": data_status()["counts"]}


@app.get("/api/cases")
def recent_cases():
    return [run for run in all_runs() if not run["replay"]]


@app.post("/api/cases")
def start_case(body: CaseRequest):
    matches = cases.candidates(body.question)
    if body.ocid:
        matches = [hit for hit in matches if hit["ocid"] == body.ocid]
    if not matches:
        return {"status": "not_found", "candidates": [], "detail": "No matching downloaded live process. No reference guessed."}
    if len(matches) != 1:
        return {"status": "ambiguous", "candidates": matches, "detail": "Choose the exact process to review"}
    if not matches[0]["award_count"]:
        return {"status": "tender_stage", "candidates": matches, "detail": "This is a tender-stage record with no published awards. Open its source record; award analysis is unavailable."}
    if not capabilities()["can_start_review"]:
        raise HTTPException(status_code=503, detail="Model not configured. Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY on the host.")
    with _INTAKE_LOCK:
        if sum(run["phase"] in ("queued", "running", "filing") for run in recent_cases()) >= 10:
            raise HTTPException(status_code=429, detail="Review queue is full. Try again after current reviews finish.")
        return PublicJSON({"run_id": cases.create_case(body.question, matches[0])}, status_code=202)


@app.get("/api/runs/{run_id}/metrics")
def run_metrics(run_id: str):
    _, path = run_paths(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return build_run_metrics(run_id)


@app.get("/api/runs/{run_id}/metrics/download")
def metrics_download(run_id: str):
    return PublicJSON(run_metrics(run_id), headers={"Content-Disposition": 'attachment; filename="run_metrics.json"'})


@app.get("/api/runs/{run_id}/audit/download")
def audit_download(run_id: str):
    _, path = run_paths(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    events = read_events(path)
    if redact(events) != events:
        raise HTTPException(status_code=409, detail="Legacy log contains sensitive material. Export is blocked; an administrator must migrate it without exposing secrets.")
    return FileResponse(path, media_type="application/x-ndjson", filename=f"{run_id}.jsonl")


@app.get("/api/runs/{run_id}/verify")
def verify_run(run_id: str):
    _, path = run_paths(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    valid, error = verify_chain(path)
    return {"run_id": run_id, "valid": valid, "error": error}


@app.get("/api/runs/{run_id}/report/download")
def report_download(run_id: str):
    report_path, _ = run_paths(run_id)
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return PublicJSON(read_report(report_path), headers={"Content-Disposition": 'attachment; filename="investigation_report.json"'})


@app.get("/api/economics")
def economics():
    from audit.metrics import aggregate_economics
    return aggregate_economics([run["run_id"] for run in recent_cases()])


@app.get("/connectors")
@app.get("/connectors/")
def connectors_page() -> FileResponse:
    target = REPO_ROOT / "app.html"
    if not target.exists():
        target = PUBLIC_DIR / "app.html"
    return FileResponse(target)


@app.get("/architecture")
def architecture_page():
    return index()


@app.get("/api/architecture")
def architecture():
    from scripts.export_architecture import architecture_svg
    return {"svg": architecture_svg()}


@app.get("/api/runs/{run_id}/sequence")
def run_sequence(run_id: str):
    run_paths(run_id)
    from scripts.export_architecture import run_sequence_svg
    svg, detail = run_sequence_svg(run_id)
    if not svg:
        raise HTTPException(status_code=404, detail=detail)
    return {"svg": svg}


@app.middleware("http")
async def security_boundary(request: Request, call_next):
    import base64
    import secrets
    password = os.getenv("BIDBOX_ACCESS_PASSWORD", "")
    if password and request.url.path != "/health":
        supplied = request.headers.get("authorization", "")
        expected = "Basic " + base64.b64encode(f"bidbox:{password}".encode()).decode()
        if not secrets.compare_digest(supplied, expected):
            return PublicJSON({"detail": "Institution access credentials required"}, status_code=401, headers={"WWW-Authenticate": 'Basic realm="BidBox", charset="UTF-8"'})
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and urlsplit(origin).netloc != request.url.netloc:
        return PublicJSON({"detail": "Cross-origin mutations are not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")
