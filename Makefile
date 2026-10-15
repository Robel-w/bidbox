.DEFAULT_GOAL := help
.PHONY: help setup test eval eval-tools demo seed inspect download server lint typecheck check clean

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*##' Makefile

setup: ## Install the project and development dependencies
	python -m pip install -e ".[dev]"

seed:
	python scripts/seed_data.py

download:
	python scripts/download_data.py

server:
	python -m bidbox_mcp.server

test:
	python -m pytest

eval:
	python evaluation/run_evals.py

eval-tools:
	python evaluation/tool_checks.py

demo:
	python scripts/run_demo.py --ocid ocds-synth-kenya-000100

inspect: ## Run the MCP server for inspection
	python -m bidbox_mcp.server

lint:
	ruff check .

typecheck: ## Run mypy on application packages
	mypy bidbox bidbox_mcp audit agent

check: lint typecheck test ## Run lint, typecheck, and tests

clean: ## Remove local caches and build outputs
	python -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache','.mypy_cache','.ruff_cache','build','dist']]"
