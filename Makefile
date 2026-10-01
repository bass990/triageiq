.PHONY: install test lint regression eval-dry eval-small eval eval-report baseline record-demo \
        backend frontend build-ui docker-build docker-demo docker-live clean help

PYTHON ?= python

help:
	@echo "TriageIQ make targets:"
	@echo ""
	@echo "  Dev / pipeline:"
	@echo "    backend        uvicorn backend.main:app --reload --port 8001"
	@echo "    frontend       npm run dev (frontend/, port 3001)"
	@echo "    build-ui       npm run build (served by the API from frontend/dist)"
	@echo ""
	@echo "  Quality (no model calls):"
	@echo "    test           pytest tests/ (fake Anthropic client)"
	@echo "    lint           ruff check backend eval tests scripts"
	@echo "    regression     latest eval snapshot vs eval/baseline.json"
	@echo ""
	@echo "  Eval harness (LLM calls, real money):"
	@echo "    eval-small     5 scenarios x 3 branches x 1 rep (~\$$0.50)"
	@echo "    eval           30 scenarios x 3 branches x 3 reps (~\$$8); add --resume to continue a stopped run"
	@echo "    eval-report    re-score and re-render the latest snapshot"
	@echo "    baseline       freeze the latest complete run as the regression floor"
	@echo "    record-demo    one live run per demo patient -> demo/traces/ (~\$$0.16)"
	@echo ""
	@echo "  Docker:"
	@echo "    docker-demo    build + run on :8001 replaying the recorded run (no key)"
	@echo "    docker-live    build + run on :8001 with ANTHROPIC_API_KEY from .env"

install:
	$(PYTHON) -m pip install -e ".[dev]"

backend:
	$(PYTHON) -m uvicorn backend.main:app --reload --port 8001

frontend:
	cd frontend && npm run dev

build-ui:
	cd frontend && npm ci --no-audit --no-fund && VITE_API_URL="" npm run build

test:
	$(PYTHON) -m pytest tests/ -q

lint:
	$(PYTHON) -m ruff check backend/ eval/ tests/ scripts/

regression:
	$(PYTHON) -m eval.regression_check

eval-dry:
	$(PYTHON) -m eval.runners --mode dry

eval-small:
	$(PYTHON) -m eval.runners --mode small --yes

eval:
	$(PYTHON) -m eval.runners --mode full --yes

eval-report:
	$(PYTHON) -m eval.runners --rerender

baseline:
	$(PYTHON) -m eval.regression_check --write-baseline

record-demo:
	$(PYTHON) scripts/record_demo.py --all

docker-build:
	docker build -t triageiq .

docker-demo: docker-build
	docker run --rm -p 8001:8001 -e TRIAGEIQ_DEMO=1 triageiq

docker-live: docker-build
	docker run --rm -p 8001:8001 --env-file .env -v "$(CURDIR)/logs:/app/logs" triageiq

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
