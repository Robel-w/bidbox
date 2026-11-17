"""Tool to generate auditable committee investigation reports."""

import json
from datetime import UTC, datetime
from pathlib import Path

from audit.events import get_run_cost, require_report_approval, report_content, ApprovalRequiredError
from bidbox_mcp.schemas.investigation import DraftFlag
from bidbox.connectors.redaction import redact
from audit.logger import audited, get_audit_logs
from bidbox_mcp.schemas.investigation import InvestigationFileResult
from bidbox_mcp.tools.flag_finding import get_draft_flags

FOOTER_TEXT = "Prepared for committee decision. No award decision is made or implied."
BASE_LIMITATIONS = [
    "Analyses rely solely on completed historical procurement awards (OCDS data).",
    "An anomaly constitutes a flag for human verification, not misconduct or liability.",
]


@audited("create_investigation_file")
def create_investigation_file_tool(
    run_id: str,
    approver_name: str | None = None,
    approver_role: str = "Procurement Review Officer",
    limitations: list[str] | None = None,
) -> InvestigationFileResult:
    """Render finalized committee investigation Markdown and JSON report with governance signoff."""
    approval = require_report_approval(run_id, approver_name)
    approver_name = str(approval["inputs"]["approver_name"]).strip()
    approver_role = str(approval["inputs"].get("approver_role") or approver_role)

    approved = report_content(run_id)
    if "contract" in approved and limitations is not None and redact(limitations) != approved.get("limitations", []):
        raise ApprovalRequiredError("Report limitations differ from the approved content")
    flags = get_draft_flags(run_id)
    if "contract" in approved:
        flags = [DraftFlag(flag_id=f"REVIEW-{index}", run_id=run_id, ocid=approved["contract"]["ocid"],
                           type=f["category"], severity=f.get("severity", "medium"), statement=f["message"],
                           rationale=f["rationale"], uncertainty=f["uncertainty"], follow_up=f["follow_up"], sources=f["sources"])
                 for index, f in enumerate(approved.get("findings", []), 1)]
    out_dir = Path("reports/examples") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_calls = get_audit_logs(run_id)
    review_summary = next((e["outputs"] for e in reversed(audit_calls) if e.get("tool") == "evidence_review"), None)
    now_str = datetime.now(UTC).isoformat()
    data_limitations = [*BASE_LIMITATIONS, *dict.fromkeys(limitations or [])]

    # Build Markdown document
    lines: list[str] = [
        "# BidBox Investigation Report",
        "",
        f"**Run ID:** `{run_id}`  ",
        f"**Authorized By:** {approver_name} ({approver_role})  ",
        f"**Generated At (UTC):** {now_str}  ",
        "",
        "## Executive Summary",
        f"This file compiles {len(flags)} evidence-backed observation(s) for committee review.",
        "",
        "## Evidence Review",
    ]
    if review_summary is not None:
        lines.extend([
            f"Candidates reviewed: {review_summary['candidate_count']}; source-verified: {review_summary['verified_count']}; retained: {review_summary['retained_count']}.",
            f"Reviewer status: {review_summary['status']}. Reason: {review_summary['reason']}",
            *[f"- Dropped: {item['finding'].get('code', 'unknown') if isinstance(item['finding'], dict) else item['finding']} — {item['reason']}" for item in review_summary.get("drops", [])],
        ])
    else:
        lines.append("Independent reviewer counts and reason were not recorded. Manual evidence review is required.")
    lines.extend(["", "## Findings"])

    if not flags:
        lines.append("*No draft findings were filed for this investigation.*")

    for idx, f in enumerate(flags, 1):
        sources_str = ", ".join(
            f"{s.ocid} ({s.field_path})" for s in f.sources
        )
        lines.extend(
            [
                f"### Finding {idx}: {f.statement}",
                f"- **Finding:** {f.statement}",
                f"- **Evidence:** Record sources: {sources_str}",
                f"- **Context:** OCID {f.ocid} (Severity: {f.severity.upper()})",
                f"- **Potential significance:** {f.rationale}",
                f"- **Uncertainty:** {f.uncertainty}",
                f"- **Recommended follow-up:** {f.follow_up}",
                "",
            ]
        )

    cost_info = get_run_cost(run_id)

    lines.extend(
        [
            "## Data Limitations",
            *[f"- {item}" for item in data_limitations],
            "",
            "## Tool-Call Audit Appendix",
            f"Total logged actions in run: {len(audit_calls)}",
            "",
            "```json",
            json.dumps(audit_calls, indent=2, default=str),
            "```",
            "",
            f"**Execution Cost:** {cost_info.cost_display} ({cost_info.note})",
            f"**Total Tokens:** {cost_info.total_tokens if cost_info.total_tokens is not None else 'not reported'}",
            "",
            "---",
            FOOTER_TEXT,
            "",
        ]
    )

    md_path = out_dir / "investigation_report.md"
    json_path = out_dir / "investigation_report.json"

    md_content = "\n".join(lines)
    with open(md_path, "w", encoding="utf-8") as f_md:
        f_md.write(md_content)

    json_payload = {
        "run_id": run_id,
        "report_hash": approval["inputs"]["report_hash"],
        "origin": (approved.get("contract") or {}).get("origin"),
        "approver_name": approver_name,
        "approver_role": approver_role,
        "generated_at_utc": now_str,
        "findings": [f.model_dump() for f in flags],
        "review_summary": review_summary,
        "dropped_findings": review_summary.get("drops", []) if review_summary else [],
        "data_limitations": data_limitations,
        "audit_calls": audit_calls,
        "footer": FOOTER_TEXT,
    }

    with open(json_path, "w", encoding="utf-8") as f_json:
        json.dump(json_payload, f_json, indent=2, default=str)

    return InvestigationFileResult(
        run_id=run_id,
        markdown_path=str(md_path),
        json_path=str(json_path),
        approver_name=approver_name,
        approver_role=approver_role,
        status="finalized",
        findings_count=len(flags),
    )
