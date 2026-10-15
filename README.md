# BidBox

BidBox prepares an auditable, evidence-backed procurement review of completed public awards.

**What it does:** benchmarks award prices, inspects company-level supplier history, records data-quality limits, and prepares a committee report. **What it does not do:** it never awards a tender, ranks suppliers for award, or makes a winner decision. **Scope:** synthetic OCDS-shaped award data only.

## Try it in 2 minutes

```powershell
make setup
make demo
python scripts/build_site.py
```

The demo uses the local OpenAI-compatible model in `LLM_MODEL` (default `qwen2.5:7b-instruct`); Ollama is the documented local endpoint. The demo pauses for a named approver, then writes `reports/examples/<run_id>/` and an audit log. To inspect the MCP server: `make inspect`. To approve a run from another terminal: `bidbox-approve <run_id> --approver "Name" --role "Role"`.

MCP client configuration:

```json
{"mcpServers":{"bidbox":{"command":"python","args":["-m","bidbox_mcp.server"]}}}
```

## Built vs. not built

| Built | Not built |
|---|---|
| Price, award, and supplier analysis on synthetic OCDS-shaped data | Bid-document evaluation because these portals publish no bids |
| Sourced findings, recovery, hash-chain audit, and named approval | Live portal ingestion beyond the documented schema |
| Static replay of a real recorded run | Web UI or autonomous award selection |

## MCP tools

| Tool | Type | Purpose | Inputs |
|---|---|---|---|
| `inspect_tender` | read | Read one normalized award and data-quality flags | `ocid` |
| `benchmark_award` | read | Compare price with same-class historical awards | `ocid`, `min_comparables` |
| `investigate_supplier` | read | Summarize company-level history and buyer concentration | `supplier_id` |
| `flag_finding` | action | Store a sourced, neutral draft finding | run, award, finding fields, sources |
| `create_investigation_file` | action | Prepare the committee report after named approval | run, approver, role |

## Real demo output

```text
tool=inspect_tender ...
tool=benchmark_award ... verdict=outlier_high
tool=read_file ... releases.json
tool=flag_finding ... status=draft
Approval required ...
report: reports/examples/<run_id>/investigation_report.md
audit: (True, None)
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [EVALS.md](EVALS.md), [docs/limitations.md](docs/limitations.md), and [docs/demo.md](docs/demo.md). The test data is synthetic, contains no live tenders or personal data, and reports company-level observations only. Licensed under the repository [LICENSE](LICENSE).

## Limitations and principles

Small local models can be slow or vary in tool-calling; the real evaluation records timeouts. Comparables are grouped by classification and currency without currency conversion. Synthetic records do not represent live procurement complexity. Every finding must have sources, and every irreversible report action requires a named human approver.
