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

eval-routing:
	$(PY) ../scripts/eval/d1_routing_replay.py tests/eval/routing_v1.jsonl --threshold 0.9

seed-scopes:
	@if [ -z "$(YAML)" ]; then echo "Usage: make seed-scopes YAML=config/seeds/assignment_scopes.example.yaml [DRY=1]"; exit 1; fi
	$(PY) ../scripts/seed/seed_assignment_scopes.py $(YAML) $(if $(DRY),--dry-run,)

seed-ksm-types:
	@if [ -z "$(YAML)" ]; then echo "Usage: make seed-ksm-types YAML=config/mappings/ksm_issue_types.yaml [DRY=1] [PRUNE=1]"; exit 1; fi
	$(PY) ../scripts/seed/seed_ksm_type_mappings.py $(YAML) $(if $(DRY),--dry-run,) $(if $(PRUNE),--prune,)

e2e:
	$(PYTEST) tests/e2e -v -m e2e

test: unit integration

cov:
	$(PYTEST) tests/unit --cov=app --cov-report=html
	@echo "Open htmlcov/index.html"

openapi-dump:
	$(PY) ../scripts/openapi/dump_openapi.py ../frontend/src/api/openapi.json

openapi-check: openapi-dump
	@if ! git diff --quiet -- ../frontend/src/api/openapi.json; then \
		echo "❌ openapi.json drift — run 'make gen-types' from repo root and commit"; \
		git --no-pager diff --stat -- ../frontend/src/api/openapi.json; \
		exit 1; \
	fi
	@echo "✅ openapi.json is up to date"

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
