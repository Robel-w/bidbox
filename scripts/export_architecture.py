"""Generate docs/architecture.svg and docs/run_sequence.svg from the live registry and audit events.

Diagrams are generated, never hand-drawn: the boxes, badges and arrows come from the
connector registry and real recorded audit events.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit
import os

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

STATUS_COLORS = {
    "live": "#1a7f37", "sandbox": "#9a6700", "planned": "#57606a",
    "error": "#cf222e", "not_configured": "#8250df", "configured": "#1a7f37",
}
FONT = "font-family='Segoe UI, system-ui, sans-serif'"


def esc(text: object) -> str:
    return (str(text) if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def badge(x: float, y: float, label: str, status: str) -> str:
    color = STATUS_COLORS.get(status, "#57606a")
    width = 8 + 7.2 * len(label)
    return (f"<rect x='{x:.0f}' y='{y:.0f}' width='{width:.0f}' height='18' rx='9' fill='{color}'/>"
            f"<text x='{x + width / 2:.0f}' y='{y + 13:.0f}' text-anchor='middle' font-size='11' fill='#fff' {FONT}>{esc(label)}</text>")


def box(x: float, y: float, w: float, h: float, title: str, lines: list[str], accent: str = "#0969da") -> str:
    parts = [f"<rect x='{x:.0f}' y='{y:.0f}' width='{w:.0f}' height='{h:.0f}' rx='10' fill='#ffffff' stroke='{accent}' stroke-width='1.5'/>",
             f"<rect x='{x:.0f}' y='{y:.0f}' width='{w:.0f}' height='4' rx='2' fill='{accent}'/>",
             f"<text x='{x + 14:.0f}' y='{y + 26:.0f}' font-size='14' font-weight='600' fill='#1f2328' {FONT}>{esc(title)}</text>"]
    for index, line in enumerate(lines):
        parts.append(f"<text x='{x + 14:.0f}' y='{y + 46 + index * 17:.0f}' font-size='11.5' fill='#57606a' {FONT}>{esc(line)}</text>")
    return "".join(parts)


def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "") -> str:
    mx = (x1 + x2) / 2
    parts = [f"<line x1='{x1:.0f}' y1='{y1:.0f}' x2='{x2:.0f}' y2='{y2:.0f}' stroke='#8c959f' stroke-width='1.4' marker-end='url(#arrowhead)'/>"]
    if label:
        parts.append(f"<text x='{mx:.0f}' y='{(y1 + y2) / 2 - 6:.0f}' text-anchor='middle' font-size='10.5' fill='#57606a' {FONT}>{esc(label)}</text>")
    return "".join(parts)


def svg_header(width: int, height: int, title: str) -> str:
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
            f"<defs><marker id='arrowhead' markerWidth='9' markerHeight='7' refX='8' refY='3.5' orient='auto'>"
            f"<polygon points='0 0, 9 3.5, 0 7' fill='#8c959f'/></marker></defs>"
            f"<rect width='{width}' height='{height}' fill='#f6f8fa'/>"
            f"<text x='24' y='36' font-size='17' font-weight='600' fill='#1f2328' {FONT}>{esc(title)}</text>")


def architecture_svg() -> str:
    """System diagram from the real registry: UI -> FastAPI -> agent graph -> MCP -> connectors."""
    from bidbox.connectors.registry import load_registry

    registry = load_registry()
    statuses = registry.statuses()
    by_kind: dict[str, list[dict]] = {}
    for status in statuses:
        by_kind.setdefault(status.get("kind") or "unknown", []).append(status)

    host = urlsplit(os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")).hostname or "llm"
    model = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")
    llm_configured = bool(os.getenv("LLM_API_KEY")) or host in ("localhost", "127.0.0.1", "::1")

    kind_y: dict[str, float] = {}
    y = 250.0
    row_height = 100.0
    for kind in ("data_source", "records", "registry", "file_drop", "messaging", "payments"):
        if kind in by_kind:
            kind_y[kind] = y
            y += row_height * (len(by_kind[kind]) + 1.2)

    height = int(y + 70)
    width = 1180
    parts = [svg_header(width, height, "BidBox system architecture — generated from the live connector registry")]

    parts.append(box(30, 70, 180, 90, "Committee UI", ["vanilla JS, no build step", "hash routes, SSE timeline"]))
    parts.append(box(260, 70, 190, 90, "FastAPI backend", ["/api/cases, /api/runs", "/api/integrations", "approval gate + audit"]))
    parts.append(box(500, 70, 190, 90, "Agent graph", ["planner -> investigator", "evidence reviewer", "human gate -> filer"]))
    parts.append(box(740, 70, 180, 90, "MCP client", ["session pool", "tools/list discovery"]))
    parts.append(box(960, 70, 190, 90, "Model provider", [f"{host}", esc(model)],
                     "#1a7f37" if llm_configured else "#cf222e"))
    parts.append(badge(950, 45, "configured - unverified" if llm_configured else "not_configured", "configured" if llm_configured else "not_configured"))

    parts.append(arrow(210, 115, 260, 115, "hash routes"))
    parts.append(arrow(450, 115, 500, 115, "start run"))
    parts.append(arrow(690, 115, 740, 115, "tool calls"))
    parts.append(arrow(920, 115, 960, 115, "chat completions"))
    parts.append(arrow(740, 140, 500, 140, "results + origin"))

    # connectors by kind, wired from the MCP layer
    connector_x = 500.0
    parts.append(f"<text x='{connector_x:.0f}' y='{min(kind_y.values(), default=250) - 14:.0f}' font-size='12' font-weight='600' fill='#1f2328' {FONT}>Connectors (from bidbox/connectors/, one folder + one manifest each)</text>")
    for kind, items in by_kind.items():
        base_y = kind_y.get(kind)
        if base_y is None:
            continue
        parts.append(f"<text x='500' y='{base_y:.0f}' font-size='11' fill='#8c959f' {FONT}>{esc(kind)}</text>")
        for index, item in enumerate(items):
            item_y = base_y + 16 + index * row_height
            name = item["name"]
            status = item["status"]
            accent = {"live": "#1a7f37", "sandbox": "#9a6700", "planned": "#57606a",
                      "error": "#cf222e", "not_configured": "#8250df"}.get(status, "#57606a")
            lines = [(item.get("detail") or "")[:60]]
            if item.get("credentials"):
                lines.append("credentials: " + ", ".join(
                    f"{c['name']}{'✓' if c['present'] else '✗'}" for c in item["credentials"]))
            parts.append(box(connector_x, item_y, 330, 80, name, lines[:2], accent))
            parts.append(badge(connector_x + 336, item_y + 6, status, status))
            parts.append(arrow(740, 140, connector_x + 330, item_y + 26, "") if index == 0 else "")

    parts.append(f"<text x='24' y='{height - 24:.0f}' font-size='10.5' fill='#57606a' {FONT}>Statuses are computed from real health checks (live/sandbox) or honest absence (planned/error/not_configured). Consequential actions are gated in the connector base class.</text>")
    parts.append("</svg>")
    return "".join(parts)


def run_sequence_svg(run_id: str | None = None) -> tuple[str, str]:
    """Sequence diagram generated from one run's real audit events."""
    from audit.logger import get_audit_logs

    run_ids = [run_id] if run_id else _latest_runs(1)
    if not run_ids:
        return "", "no recorded runs found"
    run_id = run_ids[0]
    events = get_audit_logs(run_id)
    if not events:
        return "", f"no audit events for {run_id}"

    actors: list[str] = ["agent"]
    for event in events:
        tool = event.get("tool") or ""
        if event.get("actor") == "connector" and "." in tool:
            name = tool.split(".", 1)[0]
            if name not in actors:
                actors.append(name)
        elif event.get("type") == "tool_call" and tool and tool not in actors and tool not in ("approval_gate",):
            if tool not in ("llm_call", "rate_limited", "retry", "fallback_model"):
                actors.append(tool)
    if any(event.get("actor") == "human" or event.get("tool") == "request_approval" for event in events):
        actors.append("human")
    if any(event.get("tool") in ("llm_call", "rate_limited", "retry", "fallback_model") for event in events):
        actors.insert(1, "llm")

    column_width = max(120, min(170, 1000 // max(len(actors), 1)))
    width = 120 + column_width * len(actors) + 40
    height = 120 + len(events) * 34 + 60
    parts = [svg_header(width, height, f"Run sequence — {run_id} (from real audit events)")]

    xs = {name: 70 + index * column_width + column_width / 2 for index, name in enumerate(actors)}
    for name, x in xs.items():
        label = "LLM" if name == "llm" else name.replace("_", " ")
        parts.append(f"<rect x='{x - 45:.0f}' y='60' width='90' height='26' rx='6' fill='#dbeafe' stroke='#0969da'/>"
                     f"<text x='{x:.0f}' y='77' text-anchor='middle' font-size='11.5' fill='#1f2328' {FONT}>{esc(label)}</text>"
                     f"<line x1='{x:.0f}' y1='86' x2='{x:.0f}' y2='{height - 40:.0f}' stroke='#d0d7de' stroke-dasharray='4 3'/>")

    y = 108.0
    for event in events:
        y += 34
        tool = event.get("tool") or event.get("actor") or "?"
        actor = event.get("actor") or "agent"
        source = {"agent": "agent", "tool": "tool", "human": "human", "connector": "agent"}.get(actor, "agent")
        if actor == "connector" and "." in tool:
            target = tool.split(".", 1)[0]
        elif tool in xs and tool != "llm_call":
            target = tool
        elif tool == "llm_call" or tool in ("rate_limited", "retry", "fallback_model"):
            target = "llm"
        else:
            target = "human" if actor == "human" else "agent"
        if source == "human":
            source_x, target = xs.get("human", xs[actors[0]]), "agent"
            target_x = xs.get("agent", xs[actors[0]])
        else:
            source_x = xs.get(source if source in xs else "agent", xs[actors[0]])
            target_x = xs.get(target, xs.get("agent", xs[actors[0]]))
        status = event.get("status") or "ok"
        color = {"ok": "#1a7f37", "error": "#cf222e", "timeout": "#cf222e", "retry": "#9a6700",
                 "rate_limited": "#9a6700", "planned": "#57606a"}.get(status, "#57606a")
        duration = event.get("duration_ms") or 0
        label = f"{tool} · {status} · {duration:.0f}ms" + (f" · attempt {event['inputs']['attempt']}" if event.get('inputs', {}).get('attempt') else "") + (" · FAULT INJECTION" if event.get("injected") else "")
        parts.append(f"<line x1='{source_x:.0f}' y1='{y:.0f}' x2='{target_x:.0f}' y2='{y:.0f}' stroke='{color}' stroke-width='1.6' marker-end='url(#arrowhead)'/>")
        mid = (source_x + target_x) / 2
        parts.append(f"<text x='{mid:.0f}' y='{y - 5:.0f}' text-anchor='middle' font-size='10' fill='#1f2328' {FONT}>{esc(label[:64])}</text>")
        parts.append(f"<text x='{width - 30:.0f}' y='{y + 3:.0f}' text-anchor='end' font-size='8.5' fill='#8c959f' {FONT}>{esc(str(event.get('ts_utc', ''))[:19])}</text>")

    parts.append(f"<text x='24' y='{height - 18:.0f}' font-size='10.5' fill='#57606a' {FONT}>Every arrow is one recorded audit event: tool/LLM/human actions with real timestamps, durations and statuses.</text>")
    parts.append("</svg>")
    return "".join(parts), run_id


def _latest_runs(count: int) -> list[str]:
    root = REPO / "reports" / "audit"
    if not root.exists():
        return []
    paths = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [path.stem for path in paths[:count]]


def main() -> None:
    docs = REPO / "docs"
    docs.mkdir(exist_ok=True)
    architecture = architecture_svg()
    (docs / "architecture.svg").write_text(architecture, encoding="utf-8")
    sequence, used_run = run_sequence_svg()
    if sequence:
        (docs / "run_sequence.svg").write_text(sequence, encoding="utf-8")
        print(json.dumps({"architecture_svg": str(docs / "architecture.svg"),
                          "run_sequence_svg": str(docs / "run_sequence.svg"), "run": used_run}))
    else:
        print(json.dumps({"architecture_svg": str(docs / "architecture.svg"),
                          "run_sequence_svg": f"skipped: {used_run}"}))


if __name__ == "__main__":
    main()
