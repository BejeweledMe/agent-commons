# One green contract for laptops, agents and CI alike.
#
# `make check` is THE definition of "the tree is green": ci.yml calls exactly
# this target, so the local ritual and the runner can never drift apart again
# the way `ruff check src/ tests/` and `ruff format --check .` did for four
# red days in August 2026. Everything runs through `uv run --locked`, so the
# versions come from uv.lock — never from whatever happens to be on PATH.

UV ?= uv

.PHONY: check lint format-check format frontend-work-deps test sync

check: lint format-check frontend-work-deps test

lint:
	$(UV) run --locked ruff check .

format-check:
	$(UV) run --locked ruff format --check .

# Writes, unlike the checks above: use it to fix what format-check reports.
format:
	$(UV) run --locked ruff format .

frontend-work-deps:
	npm ci --ignore-scripts --prefix frontend/work

test:
	$(UV) run --locked python -m pytest -q

sync:
	$(UV) sync --locked --extra test
