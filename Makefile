.PHONY: test eval demo

test:
	python -m pytest

eval:
	python evaluation/run_evals.py

demo:
	python scripts/run_demo.py
