"""Deterministic MCP checks for the synthetic evaluation cases."""

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools


ROOT = Path(__file__).resolve().parents[1]


async def _call(tool: Any, arguments: dict[str, Any]) -> Any:
    result = await tool.ainvoke(arguments)
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
        result = result[0].get("text", result)
    if isinstance(result, str):
        return json.loads(result)
    return result


async def run(case_filter: str | None = None) -> dict[str, Any]:
    results: dict[str, Any] = {}
    case_paths = sorted((ROOT / "evaluation/cases").glob("*.json"))
    if case_filter:
        case_paths = [path for path in case_paths if path.stem == case_filter]
    client = MultiServerMCPClient({"bidbox": {"transport": "stdio", "command": sys.executable, "args": ["-m", "bidbox_mcp.server"]}})
    async with client.session("bidbox") as session:
        loaded = await load_mcp_tools(session)
        tools = {tool.name.split("__")[-1]: tool for tool in loaded}
        for case_path in case_paths:
            case = json.loads(case_path.read_text(encoding="utf-8"))
            expected = json.loads((ROOT / "evaluation/expected" / case_path.name).read_text(encoding="utf-8"))
            ocid = case["ocid"]
            inspected = await _call(tools["inspect_tender"], {"ocid": ocid})
            observed: dict[str, Any] = {"inspect": inspected}
            if "benchmark" in case.get("checks", []):
                observed["benchmark"] = await _call(tools["benchmark_award"], {"ocid": ocid})
            if "supplier" in case.get("checks", []):
                observed["supplier"] = await _call(tools["investigate_supplier"], {"supplier_id": inspected["supplier_id"]})
            assertions: list[dict[str, Any]] = []
            if "benchmark_verdict" in expected:
                assertions.append({"name": "benchmark_verdict", "passed": observed.get("benchmark", {}).get("verdict") == expected["benchmark_verdict"]})
            if "inspect_quality_flag" in expected:
                assertions.append({"name": "inspect_quality_flag", "passed": expected["inspect_quality_flag"] in observed["inspect"].get("data_quality_flags", [])})
            if "supplier_quality_flag" in expected:
                assertions.append({"name": "supplier_quality_flag", "passed": observed.get("supplier", {}).get("id_quality_flag") == expected["supplier_quality_flag"]})
            if "supplier_min_concentration" in expected:
                assertions.append({"name": "supplier_min_concentration", "passed": observed.get("supplier", {}).get("buyer_concentration_share", 0) >= expected["supplier_min_concentration"]})
            results[case_path.stem] = {"case": case, "expected": expected, "observed": observed, "assertions": assertions, "passed": all(item["passed"] for item in assertions)}
    return results


def main() -> None:
    case_filter = sys.argv[1] if len(sys.argv) > 1 else None
    if case_filter:
        results = asyncio.run(run(case_filter))
        print(json.dumps(results, indent=2, default=str))
        raise SystemExit(0 if all(item["passed"] for item in results.values()) else 1)

    failures = 0
    all_results: dict[str, Any] = {}
    for case_path in sorted((ROOT / "evaluation/cases").glob("*.json")):
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), case_path.stem],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        failures += completed.returncode != 0
        if completed.stdout:
            all_results.update(json.loads(completed.stdout))
    print(json.dumps(all_results, indent=2, default=str))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()