"""Build a self-contained replay of one recorded BidBox run."""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.logger import verify_chain


def _json_safe(value: Any) -> Any:
    """Convert values to JSON-safe structures without allowing HTML injection."""
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _pick_run(base_dir: Path, requested: str | None) -> str:
    examples = base_dir / "reports" / "examples"
    if requested:
        return requested
    candidates = [
        path.parent.name
        for path in examples.glob("*/investigation_report.json")
        if (base_dir / "reports" / "audit" / f"{path.parent.name}.jsonl").exists()
    ]
    if not candidates:
        raise SystemExit("No recorded run found. Run the demo first with `make demo`.")
    return max(candidates, key=lambda run_id: (examples / run_id / "investigation_report.json").stat().st_mtime)


def _commit_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _model(events: list[dict[str, Any]]) -> str:
    for event in events:
        model = event.get("inputs", {}).get("model")
        if model:
            return str(model)
    return "not recorded"


def _ocid(report: dict[str, Any], events: list[dict[str, Any]]) -> str:
    for event in events:
        inputs = event.get("inputs", {})
        if event.get("tool") == "flag_finding":
            return str(inputs.get("kwargs", inputs).get("ocid", "not recorded"))
    findings = report.get("findings", [])
    if findings and findings[0].get("ocid"):
        return str(findings[0]["ocid"])
    return "not recorded"


def _approval(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("tool") == "approval_gate":
            return {**event.get("outputs", {}), "ts_utc": event.get("ts_utc", "")}
    return None


def build_site(run_id: str | None = None, base_dir: Path | str = ".") -> Path:
    """Build docs/site/index.html from one real report and its audit log."""
    root = Path(base_dir).resolve()
    selected = _pick_run(root, run_id)
    report_path = root / "reports" / "examples" / selected / "investigation_report.json"
    audit_path = root / "reports" / "audit" / f"{selected}.jsonl"
    if not report_path.exists() or not audit_path.exists():
        raise SystemExit(f"Recorded run '{selected}' is incomplete: expected report JSON and audit JSONL.")
    report = _read_json(report_path)
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    verified, chain_error = verify_chain(audit_path)
    total_tokens = sum((event.get("tokens") or {}).get("total_tokens", 0) for event in events)
    total_duration = round(sum(float(event.get("duration_ms", 0)) for event in events), 2)
    payload = _json_safe({
        "run_id": selected,
        "report": report,
        "events": events,
        "model": _model(events),
        "ocid": _ocid(report, events),
        "total_tokens": total_tokens,
        "total_duration_ms": total_duration,
        "chain": {"verified": verified, "message": chain_error or "SHA-256 chain verified"},
        "approval": _approval(events),
        "commit": _commit_hash(),
        "built_at": datetime.now().astimezone().isoformat(),
    })
    embedded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    embedded = embedded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output = root / "docs" / "site" / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_html(embedded), encoding="utf-8")
    return output


def _html(payload: str) -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BidBox recorded run replay</title>
<style>
:root{color-scheme:light dark;--bg:#eef2f5;--panel:#fff;--ink:#17212b;--muted:#5d6b78;--line:#ccd5dc;--accent:#176b87;--good:#176b4d;--warn:#9a5b13;--human:#68458c} 
@media(prefers-color-scheme:dark){:root{--bg:#111820;--panel:#1b2630;--ink:#e8eef2;--muted:#a7b4bd;--line:#3a4853;--accent:#70c3df;--good:#65d0a0;--warn:#f0b35b;--human:#c8a8eb}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:20px}header{background:var(--accent);color:#fff;padding:18px;border-radius:8px}h1,h2{line-height:1.2}h1{font-size:1.35rem;margin:0 0 8px}h2{font-size:1.1rem;margin:28px 0 10px}.panel,.event,.finding{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:14px;margin:9px 0}.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:9px}.metric{border-left:3px solid var(--accent);padding:8px 10px}.label{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}.value{font-weight:650;overflow-wrap:anywhere}.badge{display:inline-block;border-radius:999px;padding:3px 9px;font-size:.8rem;font-weight:700}.verified{background:#cceedd;color:var(--good)}.failed{background:#f5d4d4;color:#9a2525}.event{border-left:4px solid var(--accent)}.event.human{border-left-color:var(--human)}.event.recovery{border-left-color:var(--warn)}summary{cursor:pointer;font-weight:650}.meta{color:var(--muted);font-size:.88rem}.wide{overflow-x:auto}.wide pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0}.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:.8rem}.finding{border-left:4px solid var(--accent)}.drop,.limit{border-left:4px solid var(--warn)}footer{color:var(--muted);margin:32px 0 8px;border-top:1px solid var(--line);padding-top:14px}@media(max-width:520px){main{padding:10px}header{border-radius:0;margin:-10px}.summary{display:block}.metric{margin:7px 0}}
</style></head><body><main><header id="banner"></header><section id="summary"></section><section><h2>Run timeline</h2><div id="timeline" class="wide"></div></section><section><h2>Findings</h2><div id="findings"></div></section><section><h2>Dropped findings</h2><div id="dropped"></div></section><section><h2>Data limitations</h2><div id="limitations"></div></section><section><h2>Named approval</h2><div id="approval"></div></section><footer id="footer"></footer></main>
<script>
const data=__PAYLOAD__;
const el=id=>document.getElementById(id);
const text=value=>{const node=document.createElement('span');node.textContent=value==null?'':String(value);return node};
const block=(label,value)=>{const div=document.createElement('div');div.className='metric';const l=document.createElement('div');l.className='label';l.textContent=label;const v=document.createElement('div');v.className='value';v.appendChild(text(value));div.append(l,v);return div};
const json=value=>JSON.stringify(value,null,2);
const safe=(value)=>value==null?'':String(value);
const sourceChips=sources=>{const wrap=document.createElement('div');wrap.className='chips';(sources||[]).forEach(source=>{const chip=document.createElement('span');chip.className='chip';chip.append(text(`${safe(source.ocid)} · ${safe(source.field_path)} · ${safe(source.source_url)}`));wrap.append(chip)});return wrap};
const field=(label,value)=>{const p=document.createElement('p');const strong=document.createElement('strong');strong.append(text(label+': '));p.append(strong,text(value));return p};
el('banner').append(text(`Recorded run from a real make demo execution on ${data.model}, ${data.report.generated_at_utc||data.built_at}. Replay, not a live agent. Prepared for committee decision; no award decision is made or implied.`));
[['run_id',data.run_id],['tender ocid',data.ocid],['model',data.model],['total duration',`${data.total_duration_ms} ms`],['tokens',data.total_tokens],['tool calls',data.events.length]].forEach(([a,b])=>el('summary').append(block(a,b)));const badge=document.createElement('span');badge.className='badge '+(data.chain.verified?'verified':'failed');badge.append(text(data.chain.verified?'audit chain verified':'audit chain failed'));const auditBlock=block('audit result','');auditBlock.querySelector('.value').replaceChildren(badge);el('summary').append(auditBlock);
data.events.forEach(event=>{const article=document.createElement('article');article.className='event '+(event.actor==='human'?'human ':'')+(event.tool==='recovery'?'recovery':'');const details=document.createElement('details');const summary=document.createElement('summary');summary.append(text(`${event.ts_utc} · ${event.actor} · ${event.tool||'event'} · ${event.duration_ms} ms`));const meta=document.createElement('div');meta.className='meta';meta.append(text('Inputs: '+json(event.inputs)+' | Outputs: '+json(event.outputs)));details.append(summary,meta);article.append(details);el('timeline').append(article)});
(data.report.findings||[]).forEach(finding=>{const card=document.createElement('article');card.className='finding';card.append(field('Finding',finding.statement||finding.message),field('Evidence',finding.rationale),field('Context',finding.ocid),field('Potential significance',finding.rationale),field('Uncertainty',finding.uncertainty),field('Recommended follow-up',finding.follow_up));const priority=document.createElement('p');priority.append(text('Review priority: '+safe(finding.severity||'not recorded')));card.append(priority,sourceChips(finding.sources));el('findings').append(card)});if(!el('findings').childNodes.length)el('findings').append(text('No kept findings were recorded for this run.'));
(data.report.dropped_findings||[]).forEach(item=>{const card=document.createElement('div');card.className='panel drop';card.append(text(`${safe(item.finding||item.code)}: ${safe(item.reason)}`));el('dropped').append(card)});if(!el('dropped').childNodes.length)el('dropped').append(text('No dropped findings were recorded in the report.'));
(data.report.data_limitations||[]).forEach(item=>{const p=document.createElement('div');p.className='panel limit';p.append(text(item));el('limitations').append(p)});if(!el('limitations').childNodes.length)el('limitations').append(text('No limitations were recorded in the report.'));
if(data.approval){const card=document.createElement('div');card.className='panel';card.append(field('Approver',data.approval.approver_name),field('Role',data.approval.approver_role),field('Decision',data.approval.decision),field('Reason',data.approval.reason),field('Timestamp',data.approval.ts_utc));el('approval').append(card)}else el('approval').append(text('No named approval event was recorded for this run.'));
el('footer').append(text(`Synthetic data. No live tenders, no personal data. Prepared for committee decision. No award decision is made or implied. Commit: ${data.commit}`));
</script></body></html>""".replace("__PAYLOAD__", payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a static replay of a recorded BidBox run.")
    parser.add_argument("--run", help="Recorded run ID; defaults to the most recent complete run.")
    args = parser.parse_args()
    print(build_site(args.run))


if __name__ == "__main__":
    main()
