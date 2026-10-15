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

## Success looks like

BidBox should help a procurement committee:

- Spend less time searching across disconnected records
- Notice potentially important patterns earlier
- Ask more precise follow-up questions
- Compare evidence more consistently
- Produce clearer, better-sourced review records
- Keep final authority and accountability with humans

The measure of success is not how many alerts the agent produces. It is whether the agent helps people make better-informed, fairer, and more accountable procurement decisions.

## Status

BidBox is currently an early-stage concept and prototype. This README describes the intended product direction; implementation details, supported data sources, and evaluation criteria will evolve as the project develops.

## Getting Started

### Prerequisites
- Python 3.12 or higher
- pip (Python package manager)

### Installation

```bash
# Clone the repository
git clone <your-repo-url>
cd bidbox

# Install dependencies
pip install -e .
```

### Running the Demo

```bash
# Run the demonstration
python scripts/run_demo.py

# Or specify a specific procurement ID
python scripts/run_demo.py --procurement-id TENDER-2024-001

# Or specify a custom output directory
python scripts/run_demo.py --output-dir ./reports
```

### Running Evaluation Tests

```bash
python scripts/test_evals.py
```

### Generating Synthetic Data

```bash
# Generate synthetic procurement fixtures
python scripts/seed_data.py

# Download sample OCDS data (offline fallback if network unavailable)
python scripts/download_data.py
```

## Project Structure

```
bidbox/
├── agent/              # Agent state, planning, and orchestration
├── apps/               # Web application
├── audit/              # Audit event logging
├── data/               # Procurement data models and samples
├── docs/               # Documentation and design decisions
├── evaluation/         # Evaluation cases and metrics
├── mcp/                # Model Context Protocol tools
├── reports/            # Report templates and examples
├── scripts/            # Utility scripts
├── tests/              # Unit and integration tests
├── ARCHITECTURE.md     # System architecture
├── CONTRIBUTING.md     # Contribution guidelines
├── EVALS.md           # Evaluation documentation
├── pyproject.toml     # Project configuration
└── README.md          # This file
```

## Contributing

Contributions should preserve BidBox's human-in-the-loop design, evidence traceability, and responsible handling of procurement data. Add or update tests and documentation with behavioral changes.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
