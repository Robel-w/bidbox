.PHONY: test eval eval-tools demo seed download server lint

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
	python scripts/run_demo.py

lint:
	ruff check .
