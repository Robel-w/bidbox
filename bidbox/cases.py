"""Durable case state around the existing investigation graph."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from filelock import FileLock

from audit.events import content_hash, report_content
from audit.logger import append_audit_event, get_audit_logs
from audit.metrics import write_run_metrics
from bidbox.connectors.redaction import redact
from bidbox.data import search_processes

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bidbox-case")


def case_path(run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run_id):
        raise ValueError("Invalid run identifier")
    return Path("runs") / run_id / "case.json"


def load_case(run_id):
    path = case_path(run_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_case(run_id, **updates):
    path = case_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix(".lock")), timeout=15):
        data = {**(load_case(run_id) or {}), **redact(updates), "run_id": run_id}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, default=str), encoding="utf-8")
        temporary.replace(path)
    return data


def candidates(question):
    reference = re.search(r"\bocds-[A-Za-z0-9_-]+", question)
    if reference:
        return search_processes(reference[0], 50)["hits"]
    result = search_processes(question[:240], 50)["hits"]
    if result:
        return result
    stop = {"review", "check", "the", "a", "an", "for", "of", "please", "price", "pricing", "supplier", "history", "and", "both", "cost", "is", "this", "award", "contract", "benchmark"}
    words = [word for word in re.findall(r"[\w-]+", question.lower()) if word not in stop]
    if not words:
        return []
    return search_processes(" ".join(words)[:240], 50)["hits"]


def create_case(question, selected):
    run_id = "case-" + uuid4().hex[:20]
    state = {"run_id": run_id, "ocid": selected["ocid"], "user_request": question,
             "step_budget": 6, "steps_used": 0, "limitations": []}
    save_case(run_id, phase="queued", question=question, ocid=selected["ocid"], state=state, replay=False)
    append_audit_event(run_id, "agent", "state", outputs={"phase": "queued", "summary": "Case accepted for evidence review"}, origin=selected["origin"])
    append_audit_event(run_id, "agent", "search_awards", {"source": "user intake", "query": question},
                       {"selected": selected, "summary": "Exact candidate selected; no identifier guessed"}, status="ok", origin=selected["origin"])
    submit_case(run_id)
    return run_id


def submit_case(run_id, filing=False):
    return _WORKER.submit(lambda: asyncio.run(execute_case(run_id, filing)))


async def execute_case(run_id, filing=False):
    from agent.graph import build_graph, filer
    from agent.investigator import close_tools
    from agent.llm import check_model_available
    case = load_case(run_id)
    try:
        phase = "filing" if filing else "running"
        save_case(run_id, phase=phase)
        append_audit_event(run_id, "agent", "state", outputs={"phase": phase})
        if filing:
            approval = next(e["inputs"] for e in reversed(get_audit_logs(run_id)) if e.get("tool") == "approval_gate")
            if approval.get("decision") != "approve":
                raise ValueError("Report filing requires approval")
            await filer({**case["state"], **approval})
            phase = "done"
        else:
            await check_model_available()
            graph = build_graph()
            result = await graph.ainvoke(case["state"], {"configurable": {"thread_id": run_id}})
            phase = "awaiting_approval" if "__interrupt__" in result else "done"
            save_case(run_id, state={key: value for key, value in result.items() if not key.startswith("__")})
        save_case(run_id, phase=phase)
        append_audit_event(run_id, "agent", "done" if phase == "done" else "state", outputs={"phase": phase})
        if phase == "awaiting_approval":
            await asyncio.to_thread(notify_approver, run_id)
    except Exception as exc:
        message = redact(f"{type(exc).__name__}: {exc}")
        save_case(run_id, phase="failed", error=message)
        append_audit_event(run_id, "agent", "error", outputs={"phase": "failed", "summary": message}, status="error")
    finally:
        write_run_metrics(run_id)
        await close_tools()


def recover_cases():
    for path in Path("runs").glob("*/case.json"):
        case = load_case(path.parent.name)
        if case["phase"] in ("queued", "running", "filing"):
            save_case(case["run_id"], phase="interrupted", error="Service restarted during work; no effects have been automatically retried.")
            append_audit_event(case["run_id"], "agent", "error", outputs={"phase": "interrupted", "summary": "Restart interrupted this case. Start a new review; check for an existing file first."}, status="error")


def issue_invitation(run_id):
    key = os.getenv("APPROVAL_SIGNING_KEY", "")
    if len(key) < 32:
        raise ValueError("APPROVAL_SIGNING_KEY must contain at least 32 characters")
    expires = int(time.time()) + 3600
    nonce = secrets.token_urlsafe(18)
    digest = content_hash(report_content(run_id))
    raw = f"{run_id}.{expires}.{nonce}.{digest}"
    signature = hmac.new(key.encode(), raw.encode(), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{raw}.{signature}".encode()).decode().rstrip("=")
    save_case(run_id, invitation={"digest": hashlib.sha256(token.encode()).hexdigest(), "expires": expires, "used": False, "report_hash": digest})
    return token


def validate_invitation(run_id, token):
    invitation = (load_case(run_id) or {}).get("invitation", {})
    if not invitation or invitation.get("used") or invitation["expires"] <= time.time():
        raise ValueError("Approval link is missing, expired or already used")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        payload, signature = raw.rsplit(".", 1)
        expected = hmac.new(os.environ["APPROVAL_SIGNING_KEY"].encode(), payload.encode(), hashlib.sha256).hexdigest()
        valid = hmac.compare_digest(expected, signature) and payload.split(".", 1)[0] == run_id
    except (ValueError, KeyError, UnicodeError):
        valid = False
    if not valid or not hmac.compare_digest(invitation["digest"], hashlib.sha256(token.encode()).hexdigest()):
        raise ValueError("Invalid approval link")
    if invitation["report_hash"] != content_hash(report_content(run_id)):
        raise ValueError("The evidence changed; a new invitation is required")


def notify_approver(run_id):
    from bidbox.connectors.registry import load_registry
    name = os.getenv("APPROVAL_CONNECTOR", "")
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    try:
        if name not in ("smtp_email", "africastalking_sms") or not base or not os.getenv("APPROVAL_SIGNING_KEY"):
            raise ValueError("Set APPROVAL_CONNECTOR, PUBLIC_BASE_URL and APPROVAL_SIGNING_KEY to send invitations")
        url = urlsplit(base)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")):
            raise ValueError("PUBLIC_BASE_URL must use HTTPS outside localhost")
        connector = load_registry().get(name)
        health = connector.health_check()
        if health["status"] not in ("live", "sandbox"):
            raise ValueError(health["detail"])
        token = issue_invitation(run_id)
        result = connector.perform(run_id, "send_approval_link", {"case_id": run_id, "link": f"{base}/app#approval/{run_id}/{token}"})
        output = {"connector": name, "status": result.get("status", "unknown"), "result": result}
    except Exception as exc:
        output = {"connector": name or None, "status": "not_configured" if isinstance(exc, ValueError) else "error", "detail": redact(str(exc))}
    append_audit_event(run_id, "agent", "notification", outputs=output)
    save_case(run_id, notification=output)
