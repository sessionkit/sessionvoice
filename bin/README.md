# Waiters: waking a seat on what the user says

An agent seat arms a waiter in the background; it exits with one JSON line when a rule
matches, and the seat re-arms it every time it fires.

- `heard-first` blocks until a rule matches in the heard log. The rules are `turn`,
  `partial`, `first` (the user's first partial since their last turn), `cut:AGENT` and `reply:AGENT`,
  and a regex can be added. Also:
  - `--speakers alice,bob` or `'!carol'`: only lines the speaker side-car names count.
  - The echo rule: this machine's mouth, heard back, is never the user.
  - `--reply-window S` (default 120): the user's words count as a reply only within S
    seconds of the end of the line they'd be answering.
  - `--state FILE`: a re-arm catches up on what landed while the waiter was down, with
    no gaps.
- `ears-turn SEQ|FIRE_FILE` waits for the whole turn after a fire and prints one line,
  `HH:MM:SS SEQ SPEAKER: TEXT`. A waiter fires on "Hey Carol, can we..." so the seat can
  answer fast. This hands it the sentence without reading the log by hand.

- `ears-arm <seat> [talk|alone|room]` is the usual way to arm `heard-first` for a
  seat, in a mode. `ears-count <seat>` counts the waiters already up for it; run it first.
  Both are shims that exec the ears skill's scripts, so the one implementation stays in
  `skills/ears/scripts/`, and that path still works.

Tests: `uv run --no-project --with pytest python -m pytest tests/test_waiters.py`; the
shims: `make test ARGS=tests/test_waiter_shims.py`

## Launch agents: `render-plist`

`render-plist launchd/NAME.plist.in` prints that launch agent with this machine's paths
filled in: `@HOME@` from `$HOME`, `@EARS_REPO@` this checkout, `@PYTHON@` its `.venv`
(`--set NAME=VALUE` or `NAME` in the environment overrides; `-o DIR` writes files).
`@TALK@` (the Talk agent's installed `talk`) has no default: set it. It
never calls `launchctl` and never writes `~/Library/LaunchAgents` itself: each
template's header says how to install it by hand. Placeholders named inside a
`<!-- -->` header are left as written, so a path holding `--` still renders a valid plist.

Tests: `make test ARGS=tests/test_launchd_templates.py`

## The architecture page: `build-architecture-page`

`build-architecture-page` writes `docs/architecture.html` from `docs/architecture.md`:
the page with the diagrams, rendered in the browser. Run it after editing the Markdown;
`tests/test_docs_layout.py` fails while the page is stale.
