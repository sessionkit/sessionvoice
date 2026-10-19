"""EARS-333 (D8): no persona name is a behaviour default in shipped code.

The inventory (the task's reviews/, d8-inventory) found two defaults; each is
pinned neutral here:

- ``bench/clone_live_ab.py --voice`` defaulted to one seat's clone voice. Clone
  voices are per host, so there is no neutral one: the flag is required, and a
  run without it refuses before anything is loaded or said.
- the "Ears: live" page (``view/src/index.html``) labelled every agent line with
  one seat's name. The label now comes from ``view/push`` (``EARS_VIEW_LABEL``,
  sent as the payload's ``label``) and is the neutral "Agent" when unset.

No mouth, no server, no network: the bench refuses at argument checks, and
``view/push`` is loaded as a module (its loop runs only under ``__main__``).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLONE_AB = ROOT / "bench" / "clone_live_ab.py"
PUSH = ROOT / "view" / "push"
PAGE = ROOT / "view" / "src" / "index.html"


def run_ab(*args, tmp_path):
    return subprocess.run([sys.executable, str(CLONE_AB), "--json", str(tmp_path / "out.jsonl"), *args],
                          capture_output=True, text=True, timeout=60, cwd=tmp_path)


def test_clone_ab_voice_is_required(tmp_path):
    r = run_ab(tmp_path=tmp_path)
    assert r.returncode == 2, r.stderr
    assert "--voice" in r.stderr and "required" in r.stderr
    assert not (tmp_path / "out.jsonl").exists()


def test_clone_ab_given_voice_passes_argument_parsing(tmp_path):
    # --burners over the cap is refused by the bench's own first check, after parsing:
    # so a refusal naming burners proves --voice X parsed, without loading any model.
    r = run_ab("--voice", "any-clone", "--burners", "99", tmp_path=tmp_path)
    assert r.returncode == 1, r.stderr
    assert "refused: 99 burners" in r.stderr


def load_push(monkeypatch, label):
    if label is None:
        monkeypatch.delenv("EARS_VIEW_LABEL", raising=False)
    else:
        monkeypatch.setenv("EARS_VIEW_LABEL", label)
    loader = importlib.machinery.SourceFileLoader("ears_view_push", str(PUSH))
    spec = importlib.util.spec_from_loader("ears_view_push", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_push_label_unset_is_null(monkeypatch):
    push = load_push(monkeypatch, None)
    p = push.make_payload({"cap": True}, "", [["00:00:01", "c", "hello", None]], {"n": 0})
    assert p["label"] is None
    assert p["items"] == [["00:00:01", "c", "hello", None]]
    assert set(p) == {"at", "st", "live", "items", "m", "label"}


def test_push_label_empty_is_null(monkeypatch):
    assert load_push(monkeypatch, "").make_payload({}, "", [], {})["label"] is None


def test_push_label_comes_from_env(monkeypatch):
    assert load_push(monkeypatch, "Robin").make_payload({}, "", [], {})["label"] == "Robin"


def page_code_lines():
    """index.html without its comment lines: the markup, the CSS and the script that runs."""
    return [ln for ln in PAGE.read_text().splitlines() if not ln.strip().startswith("//")]


def test_page_labels_agent_lines_from_payload_with_neutral_fallback():
    code = "\n".join(page_code_lines())
    assert 'var label = p.label || "Agent";' in code
    # the one place a "c" line gets its name: the payload's label, escaped, and nothing typed in
    who = [ln.strip() for ln in page_code_lines() if 'i[1] === "c"' in ln]
    assert who == ["""var who = i[1] === "c" ? '<span class="agent">' + esc(label) + ': ' : '<span>';"""]
