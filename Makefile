# One command runs the whole suite, offline: `make test`.
# `uv sync` once (it reads uv.lock and installs the `dev` group); after that
# nothing here touches the network. Extra pytest flags: make test ARGS="-k mouth"
.PHONY: sync test

sync:
	uv sync --locked

test:
	uv run --locked pytest $(ARGS)

# The pre-publish scrub check. The scanner and its word list are PRIVATE and never
# live in this repo: SESSIONKIT_SCAN names the release-scan tool (it finds its list,
# or takes SESSIONKIT_SCRUB_RULES). Exits non-zero on any P0 term in HEAD's tree.
.PHONY: scan scrub
scan:  ## scrub check: HEAD's tree against the private word list; fails on any P0 term
	@[ -n "$$SESSIONKIT_SCAN" ] || { echo "scan: set SESSIONKIT_SCAN to the release-scan tool" >&2; exit 2; }
	@"$$SESSIONKIT_SCAN" --here --fail-on P0

scrub: scan  ## the same check, by its other name
