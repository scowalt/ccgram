.PHONY: fmt lint lint-lazy test test-integration test-integration-llm test-e2e test-all typecheck deptry check install dev build clean archfit arch-guard arch-check arch-score arch-review

fmt:
	uv run ruff format src/ tests/

lint: lint-lazy
	uv run ruff check src/ tests/

lint-lazy:
	uv run python scripts/lint_lazy_imports.py

typecheck:
	uv run pyright src/ccgram/ tests/

deptry:
	uv run deptry src

test:
	uv run pytest tests/ -m "not integration and not e2e" -n auto --dist=worksteal

test-serial:
	uv run pytest tests/ -m "not integration and not e2e"

test-integration:
	uv run pytest tests/integration/ -m "not llm" -n auto --dist=worksteal -v

test-integration-llm:
	uv run pytest tests/integration/ -m "llm" -v

test-e2e:
	uv run pytest tests/e2e/ -v --timeout=300

test-all:
	uv run pytest tests/ -n auto --dist=worksteal -v -m "not e2e"

check: fmt lint typecheck deptry test test-integration

arch-guard:
	uv run python scripts/lint_lazy_imports.py
	uv run pytest -q \
	  tests/ccgram/test_multiplexer_boundary.py \
	  tests/ccgram/test_no_tty_outside_backend.py \
	  tests/ccgram/test_query_layer_only_for_handlers.py \
	  tests/ccgram/test_window_state_access_audit.py \
	  tests/ccgram/test_window_store_import_boundary.py \
	  tests/ccgram/test_multiplexer_contract.py \
	  tests/ccgram/test_handler_layering_invariants.py

archfit:
	@archfit analyze --config .archfit.yaml --full

arch-check:
	@command -v archfit >/dev/null 2>&1 || { echo "archfit not installed — skipping (see github.com/alexei-led/archfit)"; exit 0; }
	@archfit --gate --config .archfit.yaml --full; ec=$$?; \
	if [ $$ec -eq 0 ] || [ $$ec -eq 2 ]; then exit 0; fi; \
	echo "make: archfit drift gate FAILED (exit $$ec)"; exit $$ec

arch-score:
	@archfit analyze --config .archfit.yaml --full --format scorecard

arch-review:
	@archfit analyze --config .archfit.yaml --full --llm

install:
	uv sync

dev:
	uv sync --extra dev

build:
	uv build

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache .mypy_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
