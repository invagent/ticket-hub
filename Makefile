.PHONY: help gen-types check-types backend-test frontend-test test eval-routing

help:
	@echo "Top-level targets:"
	@echo "  gen-types    Regenerate frontend/src/api/{openapi.json,types.ts} from FastAPI"
	@echo "  check-types  CI gate: fail if openapi.json or types.ts drift from committed"
	@echo "  backend-test Run backend unit + PII gates"
	@echo "  frontend-test Run frontend type-check + unit"
	@echo "  test         backend-test + frontend-test"
	@echo "  eval-routing Run D1 routing replay against tests/eval/routing_v1.jsonl"
	@echo ""
	@echo "Per-stack: cd backend / frontend / cli and use their own Makefile / npm scripts."

# ---- type generation ------------------------------------------------------

gen-types:
	$(MAKE) -C backend openapi-dump
	cd frontend && npm run gen:api

check-types:
	$(MAKE) -C backend openapi-dump
	@if ! git diff --quiet -- frontend/src/api/openapi.json; then \
		echo "❌ openapi.json drift — run 'make gen-types' and commit"; \
		git --no-pager diff --stat -- frontend/src/api/openapi.json; \
		exit 1; \
	fi
	cd frontend && npm run check:api
	@echo "✅ openapi.json + types.ts are in sync with backend"

# ---- test pipeline --------------------------------------------------------

backend-test:
	$(MAKE) -C backend lint unit pii-cov

frontend-test:
	cd frontend && npm run type-check && npm run test

test: backend-test frontend-test

eval-routing:
	$(MAKE) -C backend eval-routing
