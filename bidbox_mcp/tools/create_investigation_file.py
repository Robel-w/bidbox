"""Tool to generate auditable committee investigation reports."""

import json
from datetime import UTC, datetime
from pathlib import Path

from audit.logger import audited, get_audit_logs
from bidbox_mcp.schemas.investigation import InvestigationFileResult
from bidbox_mcp.tools.flag_finding import get_draft_flags

FOOTER_TEXT = "Prepared for committee decision. No award decision is made or implied."


@audited("create_investigation_file")
def create_investigation_file_tool(
    run_id: str,
    approver_name: str | None = None,
    approver_role: str = "Procurement Review Officer",
) -> InvestigationFileResult:
    """Render finalized committee investigation Markdown and JSON report with governance signoff."""
    if not approver_name or not approver_name.strip():
        raise ValueError(
            "Approver name is required: irreversible report generation requires a named human gate."
        )

    out_dir = Path("reports/examples") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    flags = get_draft_flags(run_id)
    audit_calls = get_audit_logs(run_id)
    now_str = datetime.now(UTC).isoformat()

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
        "## Findings",
    ]

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

    lines.extend(
        [
            "## Data Limitations",
            "- Analyses rely solely on completed historical procurement awards (OCDS data).",
            "- An anomaly constitutes a flag for human verification, not misconduct or liability.",
            "",
            "## Tool-Call Audit Appendix",
            f"Total logged actions in run: {len(audit_calls)}",
            "",
            "```json",
            json.dumps(audit_calls, indent=2, default=str),
            "```",
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
        "approver_name": approver_name,
        "approver_role": approver_role,
        "generated_at_utc": now_str,
        "findings": [f.model_dump() for f in flags],
        "data_limitations": [
            "Historical completed awards only",
            "Screening signals for human inquiry, not misconduct determination",
        ],
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
