"""Render real agent evaluation results into EVALS.md."""

import json
from pathlib import Path
import sys


def render(path: str) -> None:
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = ["| Case | Pass | Agreement | Tokens | Cost |", "|---|---:|---:|---:|---:|"]
    for case, score in result["case_scores"].items():
        rows.append(f"| {case} | {score['passed']} | {score['agreement']} | {score['tokens']} | ${score['cost_usd']:.2f} |")
    section = "\n".join([
        "<!-- RESULTS:START -->",
        f"Model: `{result['model']}`; date: `{result['timestamp']}`; hardware: `{result['hardware']}`",
        "",
        *rows,
        "",
        f"Aggregate: {result['aggregate']}",
        "<!-- RESULTS:END -->",
    ])
    target = Path("EVALS.md")
    existing = target.read_text(encoding="utf-8") if target.exists() else "# Evaluation Results\n"
    start, end = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
    if start in existing and end in existing:
        before = existing.split(start, 1)[0]
        after = existing.split(end, 1)[1]
        existing = before + section + after
    else:
        existing += "\n" + section + "\n"
    target.write_text(existing, encoding="utf-8")


if __name__ == "__main__":
    render(sys.argv[1])