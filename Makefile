.PHONY: install test lint privacy models-recommend agent-filter track1-synthetic benchmark-synthetic

install:
	python3 -m pip install -e '.[dev]'

test:
	python3 -m pytest

lint:
	python3 -m ruff check .
	python3 -m ruff format --check .

privacy:
	python3 scripts/privacy_guard.py --all

models-recommend:
	rare-disease-agent models recommend

agent-filter:
	rare-disease-agent agent-filter --input synthetic --backend mock

track1-synthetic:
	rare-disease-agent track1-synthetic --case de-novo

benchmark-synthetic:
	rare-disease-agent benchmark-synthetic
