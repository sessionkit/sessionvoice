# One command runs the whole suite, offline: `make test`.
# `uv sync` once (it reads uv.lock and installs the `dev` group); after that
# nothing here touches the network. Extra pytest flags: make test ARGS="-k mouth"
.PHONY: sync test

sync:
	uv sync --locked

test:
	uv run --locked pytest $(ARGS)

# Releases. The ONE version lives in pyproject.toml; scripts/release.py bumps it,
# mirrors it into the plugin and marketplace manifests, stamps CHANGELOG.md,
# commits and tags. It never pushes: `make push` is separate, and so is publish.
# The order: `make release`, `make push`, push the tag to the public repo, then
# `make publish`, which builds through pypi-dist (below), never through build.
BUMP ?= patch
.PHONY: build pypi-dist release push publish

build:  ## wheel + sdist into dist/, from the checkout as it stands (not what publish uploads)
	@uv build

# PyPI shows README.md, where this repo's RELATIVE links 404: they stay relative
# in the repo (tests/test_readme_links.py), so the package page is fixed at publish
# time, not by editing the README (a one-off edit made them absolute once; the
# links came back).
# pypi-dist builds from the release TAG, never the checkout: `git archive` into a
# scratch stage, pin the stage's README links to the tag with the kit's
# scripts/pypi_readme.py, `uv build` there into dist/, then check BOTH the wheel and
# the sdist for any relative link left. The links point at the PUBLIC repo's tag,
# so the tag must be there first, at the same commit: the kit's `tag-check` refuses
# unless the local tag and PUBLIC_REMOTE's tag are one commit, and names both shas
# when they differ (a tag that exists there but was moved here would otherwise build
# a package whose links point at a different tree).
# The rewriter is the KIT's one copy (sessionkit's scripts/pypi_readme.py); this
# repo keeps none, so the kit's fixes reach every family repo at once.
# SESSIONKIT_DIR names a sessionkit checkout that has scripts/pypi_readme.py
# (environment or
# `make pypi-dist SESSIONKIT_DIR=...`); unset, or without the script, pypi-dist
# refuses with exit 2, as scan does without SESSIONKIT_SCAN. PYPI_README, the whole
# command, overrides it (tests/test_pypi_dist.py points it at a stub).
PUBLIC_REMOTE ?= https://github.com/sessionkit/sessionvoice.git
PYPI_README ?= $(if $(SESSIONKIT_DIR),python3 "$(SESSIONKIT_DIR)/scripts/pypi_readme.py")
pypi-dist: SHELL := /usr/bin/env bash
pypi-dist:  ## wheel + sdist from tag v<version>, README links pinned to it; needs SESSIONKIT_DIR; the tag must be the same commit on PUBLIC_REMOTE
	@set -euo pipefail; \
	[ -n '$(strip $(PYPI_README))' ] \
	  || { echo "pypi-dist: set SESSIONKIT_DIR to a sessionkit checkout: its scripts/pypi_readme.py pins the README links (this repo keeps no copy)" >&2; exit 2; }; \
	if [ '$(origin PYPI_README)' = file ] && [ ! -f "$(SESSIONKIT_DIR)/scripts/pypi_readme.py" ]; then \
	  echo "pypi-dist: SESSIONKIT_DIR=$(SESSIONKIT_DIR) has no scripts/pypi_readme.py; set SESSIONKIT_DIR to a sessionkit checkout that has it" >&2; exit 2; fi; \
	v="v$$(uv version --short)"; \
	git rev-parse -q --verify "refs/tags/$$v^{commit}" >/dev/null \
	  || { echo "pypi-dist: no tag $$v here; make release tags the version" >&2; exit 1; }; \
	$(PYPI_README) tag-check --tag "$$v" --remote "$(PUBLIC_REMOTE)"; \
	stage="$$(mktemp -d)"; trap 'rm -rf "$${stage:?}"' EXIT; \
	git archive "$$v" | tar -x -C "$$stage"; \
	$(PYPI_README) rewrite --repo sessionkit/sessionvoice --ref "$$v" --root "$$stage" "$$stage/README.md"; \
	(cd "$$stage" && uv build --out-dir "$(CURDIR)/dist"); \
	dist="dist/$$(echo 'sessionvoice' | tr '-' '_')-$${v#v}"; \
	$(PYPI_README) check "$$dist"-*.whl "$$dist".tar.gz

release:  ## bump (BUMP=patch|minor|major) or set (VERSION=X.Y.Z), then mirror, changelog, commit, tag
	@uv run python scripts/release.py $(or $(VERSION),$(BUMP))

push:  ## push the branch and the tags to origin (release never pushes; this is the line it printed)
	@git push origin HEAD && git push origin --tags

# THE TOKEN NEVER TOUCHES A COMMAND LINE. argv is readable by every process
# on the machine (`ps`); a child's environment only by its own user. So the
# token reaches uv as UV_PUBLISH_TOKEN in uv's environment, from one
# `pass show` (one touch), and what `ps` shows is `uv publish dist/...`.
# The process list never includes the password. PYPI_TOKEN_PASS_PATH names the pass entry, e.g.
# pypi/token; set it in your shell profile.
publish: pypi-dist  ## upload THIS version's wheel + sdist to PyPI; token via $$PYPI_TOKEN_PASS_PATH (pass) or $$UV_PUBLISH_TOKEN, never on a command line
	@dist="dist/$$(echo 'sessionvoice' | tr '-' '_')-$$(uv version --short)"; \
	if [ -n "$$UV_PUBLISH_TOKEN" ]; then uv publish "$$dist"*; \
	elif [ -n "$$PYPI_TOKEN_PASS_PATH" ]; then UV_PUBLISH_TOKEN="$$(pass show "$$PYPI_TOKEN_PASS_PATH" | head -n1)" uv publish "$$dist"*; \
	else echo "publish: set PYPI_TOKEN_PASS_PATH to the pass entry holding your PyPI token (or UV_PUBLISH_TOKEN); the token is never put on a command line" >&2; exit 1; fi

# The pre-publish scrub check. The scanner and its word list are PRIVATE and never
# live in this repo: SESSIONKIT_SCAN names the release-scan tool (it finds its list,
# or takes SESSIONKIT_SCRUB_RULES). Exits non-zero on any P0 term in HEAD's tree.
.PHONY: scan scrub
scan:  ## scrub check: HEAD's tree against the private word list; fails on any P0 term
	@[ -n "$$SESSIONKIT_SCAN" ] || { echo "scan: set SESSIONKIT_SCAN to the release-scan tool" >&2; exit 2; }
	@"$$SESSIONKIT_SCAN" --here --fail-on P0

scrub: scan  ## the same check, by its other name
