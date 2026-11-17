"""Question-driven planning using tools discovered from MCP."""

import json
import re

from audit.logger import append_audit_event
from .investigator import call_tool, load_tools
from .llm import ask_json


def question_checks(question: str) -> list[str]:
    price = bool(re.search(r"\b(price|pricing|cost|value|benchmark|expensive|overpriced)\b", question, re.I))
    supplier = bool(re.search(r"\b(supplier|vendor|company|concentration|history)\b", question, re.I))
    return (["price benchmark"] if price else []) + (["supplier history"] if supplier else []) or ["price benchmark", "supplier history"]


async def plan_investigation(state: dict) -> dict:
    tools = await load_tools()
    question = state.get("user_request", "")
    checks = question_checks(question)
    names = {"price benchmark": "benchmark_award", "supplier history": "investigate_supplier"}
    checks = [check for check in checks if names[check] in tools]
    sequence = [{"tool": "inspect_tender", "reason": "Retrieve the exact selected award and its provenance"}]
    sequence += [{"tool": names[check], "reason": f"Question requests {check}"} for check in checks]
    extra = []
    connectors = {name: tool for name, tool in tools.items() if name.startswith("connector_") and name.lower() in question.lower()}
    if connectors:
        response = await ask_json(state["run_id"],
            'Select only requested read-only connector tools. Return {"tool_calls": [{"name": string, "params": object, "reason": string}]}. Never propose a send, payment or other effect.',
            json.dumps({"question": question, "tools": [{"name": name, "description": tool.description} for name, tool in connectors.items()]}),
            {"tool_calls": []})
        from bidbox.connectors.registry import load_registry
        allowed = {s["name"] for s in load_registry().tool_specs() if not s["consequential"] and s.get("mode") == "read" and not s["planned"]}
        extra = [item for item in response.get("tool_calls", []) if isinstance(item, dict) and item.get("name") in allowed and item.get("name") in connectors and isinstance(item.get("params"), dict)][:3]
        sequence += [{"tool": item["name"], "reason": str(item.get("reason", "Requested integration read"))} for item in extra]
    append_audit_event(state["run_id"], "agent", "plan", {"source": "user question and MCP tools/list"},
                       {"tools": sequence, "summary": "Retrieve evidence, review independently, then await human approval"})
    result = await call_tool(tools, state["run_id"], "inspect_tender", {"ocid": state["ocid"]})
    tender = result.model_dump() if hasattr(result, "model_dump") else result
    return {"tender": tender, "plan": {"checks": checks, "tools": sequence, "connector_calls": extra},
            "tool_outputs": [{"tool": "inspect_tender", "inputs": {"ocid": state["ocid"]}, "output": tender}]}
