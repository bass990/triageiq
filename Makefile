.PHONY: install test lint eval-dry eval-small eval eval-report clean help

PYTHON ?= python

help:
	@echo "TriageIQ eval harness — make targets:"
	@echo "  make install       Install dev dependencies (pydantic + pytest + ruff)"
	@echo "  make test          Run all tests (zero LLM)"
	@echo "  make lint          Ruff lint check on eval/ and tests/"
	@echo "  make eval-dry      Print status, no API calls"
	@echo "  make eval-small    5 scenarios x 2 branches x 1 rep (~\$$0.50-\$$1)"
	@echo "  make eval          30 scenarios x 2 branches x 3 reps (~\$$5-10)"
	@echo "  make eval-report   Re-render the most recent eval report"
	@echo "  make clean         Remove __pycache__ and pytest caches"

# Note: PYTHONPATH is not set per-target because:
#   - pytest reads pythonpath = ["."] from pyproject.toml [tool.pytest.ini_options]
#   - eval.runners is an installed package (via `pip install -e .`) so `python -m`
#     finds it without sys.path tweaks
# This makes the Makefile cross-platform (works on Windows cmd/PowerShell too).

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests/ -q

lint:
	$(PYTHON) -m ruff check eval/ tests/

eval-dry:
	$(PYTHON) -m eval.runners --mode dry

eval-small:
	$(PYTHON) -m eval.runners --mode small

eval:
	$(PYTHON) -m eval.runners --mode full

eval-report:
	@echo "Most recent report:"
	@ls -t eval/reports/run_*.md 2>/dev/null | head -1

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
