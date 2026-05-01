.PHONY: help install lint type unit pii-cov integration eval e2e test cov clean openapi-check

PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest

help:
	@echo "Targets:"
	@echo "  install         Create venv + install deps (incl. dev)"
	@echo "  lint            ruff + mypy"
	@echo "  unit            Run unit tests, coverage gate >= 70%"
	@echo "  pii-cov         Run PII module tests, coverage gate >= 95%"
	@echo "  integration     Run integration tests (requires Docker)"
	@echo "  e2e             Run e2e tests (requires UAT credentials)"
	@echo "  eval            Run LLM evaluation suite (requires Provider keys)"
	@echo "  test            unit + integration"
	@echo "  cov             Generate HTML coverage report"
	@echo "  openapi-check   Diff backend OpenAPI vs frontend types.ts"
	@echo "  clean           Remove venv, caches, coverage"

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

lint:
	$(VENV)/bin/ruff check app tests
	$(VENV)/bin/ruff format --check app tests
	$(VENV)/bin/mypy app

unit:
	$(PYTEST) tests/unit -v --cov=app --cov-report=term-missing --cov-fail-under=70

pii-cov:
	$(PYTEST) tests/unit/core/pii -v \
		--cov=app/core/pii \
		--cov-report=term-missing \
		--cov-fail-under=95

integration:
	$(PYTEST) tests/integration -v -m integration

eval:
	$(PY) ../scripts/eval/run_eval.py tests/eval/dataset_v1.jsonl

e2e:
	$(PYTEST) tests/e2e -v -m e2e

test: unit integration

cov:
	$(PYTEST) tests/unit --cov=app --cov-report=html
	@echo "Open htmlcov/index.html"

openapi-check:
	$(PY) -c "from app.main import app; import json, sys; json.dump(app.openapi(), sys.stdout, indent=2)" > /tmp/openapi.json
	@echo "Backend OpenAPI written to /tmp/openapi.json"
	@echo "TODO: diff against frontend/src/api/types.ts (D1)"

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
