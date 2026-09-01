# One green contract for laptops, agents and CI alike.
#
# `make check` is THE definition of "the tree is green": ci.yml calls exactly
# this target, so the local ritual and the runner can never drift apart again
# the way `ruff check src/ tests/` and `ruff format --check .` did for four
# red days in August 2026. Everything runs through `uv run --locked`, so the
# versions come from uv.lock — never from whatever happens to be on PATH.

UV ?= uv
PYTEST = env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_STATE_BASE \
	-u AGENT_COMMONS_SESSION_ID \
	$(UV) run --locked python -m pytest

.PHONY: check lint format-check format frontend-work-deps frontend-gallery-deps \
	frontend-work-test frontend-gallery-test help test test-domain test-runtime \
	test-ui test-contracts sync

check: lint format-check frontend-work-test frontend-gallery-test test

lint:
	$(UV) run --locked ruff check .

format-check:
	$(UV) run --locked ruff format --check .

# Writes, unlike the checks above: use it to fix what format-check reports.
format:
	$(UV) run --locked ruff format .

frontend-work-deps:
	npm ci --ignore-scripts --prefix frontend/work

frontend-gallery-deps:
	npm ci --ignore-scripts --prefix frontend/gallery

frontend-work-test: frontend-work-deps
	npm test --prefix frontend/work

frontend-gallery-test: frontend-gallery-deps
	npm test --prefix frontend/gallery

test:
	$(PYTEST) -q

# Fast feedback targets are advisory ownership shards. They partition today's
# suite, but are not impact-complete and never replace the full `make check` gate.
test-domain:
	$(PYTEST) -q tests/core tests/domain tests/services tests/storage \
		tests/coordination tests/index tests/integrations

test-runtime:
	$(PYTEST) -q tests/runtime tests/mcp

test-ui: frontend-work-test frontend-gallery-test
	$(PYTEST) -q tests/ui tests/cli

test-contracts:
	$(PYTEST) -q tests/contract tests/e2e tests/evals tests/evals_harness \
		tests/benchmarks tests/schemas tests/security tests/test_ci_environment.py \
		tests/test_platform_support.py tests/test_test_pipeline_contract.py \
		tests/test_tutorial_contract.py

help:
	@printf '%s\n' \
		'Fast targets are advisory ownership shards, not impact-complete gates.' \
		'make test-domain     Domain, services, storage and coordination' \
		'make test-runtime    Provider runtime and scoped MCP' \
		'make test-ui         CLI and UI, including Node harnesses' \
		'make test-contracts  Cross-boundary, security and release contracts' \
		'make check           Authoritative full-tree green contract'

sync:
	$(UV) sync --locked --extra test
