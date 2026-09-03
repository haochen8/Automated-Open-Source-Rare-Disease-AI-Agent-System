.PHONY: install test lint privacy models-recommend

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
