"""EARS-439: `ears services install` for a phone-only Mac.

1. The refusal (EARS-421: mail in the install, no --agent, a wake file that names no seat) says
   what each choice means in plain words: --agent NAME for a Mac where an agent seat should be
   woken when you speak; --no-agent for a phone-only Mac (for example a Delta bot), where nobody
   is woken and the notices go to the log.
2. --help and the install docs carry the same short phone-only paragraph.
3. Before the speech weights download starts (before any plist is written), install prints one
   heads-up line: the total size, summed from SPEECH_WEIGHT_SIZES (never a hand-typed number),
   and that the speech server runs as a login service. --dry-run prints the same line. All
   weights already cached: it says so, and gives no heads-up.

The sizes are stubbed here (STUB): every number the line prints comes from them. Everything runs
inside test_services_speech's fence (a temp HOME, launchctl faked, the speech probes faked) and
test_services_prefetch's faked fetch seam: nothing is fetched, nothing real is touched."""
import os
from pathlib import Path

import pytest

from ears import services
from ears.speech import Probe
from tests import _scanlist
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_prefetch import px  # noqa: F401  (a fixture)
from test_services_speech import VM, sx  # noqa: F401  (a fixture)

ROOT = Path(__file__).resolve().parent.parent
WEIGHTS = dict(services.SPEECH_WEIGHTS)
PARAKEET, KOKORO, VOICES = (r for r, _ in services.SPEECH_WEIGHTS)

# The stub: each weight's measured sha and patterns kept (the reader holds a size to its sha), its
# bytes made up, so a number the heads-up prints can only have come from here. 834 MB in all.
STUB_BYTES = {PARAKEET: 600_000_000, KOKORO: 200_000_000, VOICES: 34_000_000}
STUB = tuple((use, sha12, STUB_BYTES[repo], files, pats)
             for (repo, _), (use, sha12, _, files, pats)
             in zip(services.SPEECH_WEIGHTS, services.SPEECH_WEIGHT_SIZES))


@pytest.fixture
def stub(px, monkeypatch):
    monkeypatch.setattr(services, "SPEECH_WEIGHT_SIZES", STUB)
    return px


def _cached(repo: str) -> None:
    """REPO whole at its expected sha in the server's cache, at its STUB size. Sparse."""
    f = services.speech_hf_cache() / ("models--" + repo.replace("/", "--"))
    for d in ("blobs", "refs", f"snapshots/{WEIGHTS[repo]}"):
        (f / d).mkdir(parents=True, exist_ok=True)
    (f / "refs" / "main").write_text(WEIGHTS[repo])
    with open(f / "blobs" / ("a" * 64), "wb") as b:
        b.truncate(STUB_BYTES[repo])


def _index(out: str, text: str) -> int:
    i = out.find(text)
    assert i >= 0, f"{text!r} not in:\n{out}"
    return i


def _flat(text: str) -> str:
    return " ".join(text.split())


# ---- 1. the refusal says what each choice means -------------------------------------------------

def _refusal(fence, monkeypatch, capsys) -> str:
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.setattr(services, "install", lambda *a, **kw: pytest.fail("install() ran after a refusal"))
    assert services.main(["install"]) == 2
    return capsys.readouterr().err


def test_the_refusal_says_agent_name_is_for_a_mac_with_a_seat_to_wake(fence, monkeypatch, capsys):
    err = _refusal(fence, monkeypatch, capsys)
    line = next(l for l in err.splitlines() if l.startswith("    ears services install --agent NAME"))
    assert line == ("    ears services install --agent NAME   # a Mac where an agent seat should be "
                    "woken when you speak: NAME is that seat, and it gets the keeper's notices")


def test_the_refusal_says_no_agent_is_for_a_phone_only_mac(fence, monkeypatch, capsys):
    err = _refusal(fence, monkeypatch, capsys)
    line = next(l for l in err.splitlines() if l.startswith("    ears services install --no-agent"))
    assert line == ("    ears services install --no-agent     # a phone-only Mac (for example a Delta "
                    "bot): nobody is woken, and the notices go to the log")


def test_the_refusal_keeps_its_shape(fence, monkeypatch, capsys):
    """EARS-421's contract stands: the REFUSED head, both runnable forms, `ears wake add NAME`,
    nothing written; only the words around the forms changed."""
    err = _refusal(fence, monkeypatch, capsys)
    lines = err.splitlines()
    assert lines[0].startswith("ears services install: REFUSED: no --agent NAME")
    assert lines[0].endswith("NOBODY would be woken when you speak. Choose the one that fits this Mac:")
    assert lines[1].startswith("    ears services install --agent NAME   # ")
    assert lines[2].startswith("    ears services install --no-agent     # ")
    assert lines[3] == "either way, `ears wake add NAME` adds a seat to wake later"
    assert lines[-1] == "nothing was written"


def test_the_refusal_names_no_task(fence, monkeypatch, capsys):
    """Public text: no task named. Tracker IDs are not private, so they are spelt here."""
    err = _refusal(fence, monkeypatch, capsys)
    for word in ("EARS-", "TM-"):
        assert word not in err


def test_the_refusal_names_no_private_word(fence, monkeypatch, capsys):
    """Public text: no seat or person named, only the example the walk asked for. The words
    come from the private scan list (EARS-406, tests/_scanlist.py), every row of it, never
    spelt here; unset, this guard skips loudly. A word planted from each row into the refusal
    must fire, so the guard proves it can. The wake file's path is this run's temp HOME, which
    carries the machine's own temp dir and user: the guard reads it as the user would, `~`."""
    home = os.environ["HOME"]
    err = _refusal(fence, monkeypatch, capsys)
    assert home in err, "the refusal no longer names the wake file under HOME"
    err = err.replace(home, "~")
    rows = _scanlist.rows()
    found = _scanlist.hits(err, rows)
    assert found == [], f"the refusal holds a word of row(s) {found}"
    missed = []
    for name, word in _scanlist.controls(rows):
        planted = f"{err}    ears services install --agent {word}\n"
        if name not in _scanlist.hits(planted, rows):
            missed.append(name)
    assert missed == [], f"the scan missed a word planted from row(s) {missed}"


# ---- 2. --help and the docs carry the same phone-only paragraph -----------------------------

def _help(capsys) -> str:
    with pytest.raises(SystemExit):
        services.main(["--help"])
    return capsys.readouterr().out


def test_the_help_flags_say_what_each_choice_means(capsys):
    out = _flat(_help(capsys))
    assert "--agent AGENT for a Mac where an agent seat should be woken when you speak" in out
    assert ("--no-agent for a phone-only Mac (for example a Delta bot): install mail with no wake "
            "seat; nobody is woken when you speak, and the notices go to the log") in out


def test_the_help_ends_with_the_phone_only_paragraph_and_its_size_is_computed(capsys, monkeypatch):
    monkeypatch.setattr(services, "SPEECH_WEIGHT_SIZES", STUB)
    out = _help(capsys)
    para = _flat(services.PHONE_ONLY.format(size="834 MB"))
    assert para in _flat(out)
    assert "install with --no-agent" in para and "login service" in para
    assert "2.9 GB" not in _flat(out)                  # the --no-prefetch help is computed too
    # its own paragraph, ahead of the settings note, each wrapped
    assert _flat(out).index(para) < _flat(out).index("A line '<new file> is missing")
    assert all(len(l) <= 80 for l in out.split("A phone-only Mac", 1)[1].splitlines())


def test_the_help_size_is_the_real_total_unstubbed(capsys):
    assert services._size(services.speech_weights_total()) == "2.9 GB"
    assert "one-time download of about 2.9 GB" in _flat(_help(capsys))


@pytest.mark.parametrize("doc", ["README.md", "docs/ears.md"])
def test_the_install_docs_carry_the_phone_only_paragraph(doc):
    text = _flat((ROOT / doc).read_text())
    assert ("A phone-only Mac (for example one that runs a Delta bot, with no agent seat to wake) "
            "installs with `--no-agent`: nobody is woken when you speak, and the keeper's notices go "
            "to the log. `--agent NAME` is for a Mac where an agent seat should be woken when you "
            "speak.") in text
    assert "runs as a login service" in text


@pytest.mark.parametrize("doc", ["README.md", "docs/ears.md", "docs/models.md",
                                 "docs/dependencies.md", "docs/speech-server.md"])
def test_a_size_the_docs_type_is_the_computed_total(doc):
    """The docs cannot compute: each "about N GB" they type for the speech models is held to
    speech_weights_total, so a re-measured size fails here until the docs follow."""
    import re
    text = (ROOT / doc).read_text()
    # The separator has sizes of its own (its venv, its working memory), not the speech
    # models': its models.md section and its dependencies.md row are dropped, each one
    # asserted present first, so the exclusion cannot go stale silently.
    if doc == "docs/models.md":
        m = re.search(r"^## HTDemucs\n.*?(?=^## )", text, re.M | re.S)
        assert m, "docs/models.md has no HTDemucs section to leave out"
        text = text[:m.start()] + text[m.end():]
    if doc == "docs/dependencies.md":
        rows = re.findall(r"^\| The separator \|.*\n", text, re.M)
        assert len(rows) == 1, "docs/dependencies.md has no single separator row to leave out"
        text = text.replace(rows[0], "")
    paras = [p for p in text.split("\n\n") if "**Memory:**" not in p]   # RSS, not a download
    total = services._size(services.speech_weights_total())
    typed = {m for p in paras for m in re.findall(r"about (\d+(?:\.\d+)? GB)", _flat(p))}
    assert typed, doc
    assert typed <= {total}, (doc, typed, total)


def test_the_module_docstring_says_it_too():
    doc = _flat(services.__doc__)
    assert ("--no-agent for a phone-only Mac (for example a Delta bot), where nobody is woken and "
            "the notices go to the log") in doc
    assert "speech_heads_up" in doc


# ---- 3. the heads-up, before the download ----------------------------------------------------

def test_the_heads_up_gives_the_stubbed_total_and_the_login_service(stub, capsys):
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    cache = services.speech_hf_cache()
    line = next(l for l in out.splitlines() if l.startswith("speech heads-up:"))
    assert line == ("speech heads-up: the speech models are a 834 MB download, fetched once, into "
                    f"{cache}, after the plists load (Ctrl-C stops the fetch; the install goes on). "
                    "The speech server runs as a login service (dev.sessionkit.voice.mlx-audio): "
                    "launchd starts it now and at every login")
    assert "2.9 GB" not in out[:out.index("speech loaded")]   # nothing hand-typed: the stub is all it read


def test_the_heads_up_comes_before_any_plist_and_before_the_fetch(stub, capsys):
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    heads = _index(out, "speech heads-up:")
    loaded = _index(out, "speech loaded")
    fetch = _index(out, "speech weights: fetching about 834 MB")
    assert heads < loaded < fetch
    assert out.count("speech heads-up:") == 1


def test_a_dry_run_prints_the_same_line(stub, capsys):
    assert services.install(["speech"], dry_run=True) == 0     # first: a dry run changes nothing
    dry = capsys.readouterr().out
    assert stub["argv"] == []                                   # and fetches nothing
    assert _index(dry, "speech heads-up:") < _index(dry, "--- ")   # before the plist it prints
    assert services.install(["speech"]) == 0
    real = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("speech heads-up:"))
    assert real in dry.splitlines()


def test_a_partial_cache_says_how_much_is_still_to_fetch(stub, capsys):
    _cached(PARAKEET)
    assert services.install(["speech"]) == 0
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("speech heads-up:"))
    assert line.startswith("speech heads-up: the speech models are a 834 MB download; about 234 MB "
                           "of it is still to fetch, fetched once")


def test_all_cached_says_so_and_skips_the_heads_up(stub, capsys):
    for r in WEIGHTS:
        _cached(r)
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert "speech heads-up:" not in out
    assert (f"speech weights: all 3 already cached in {services.speech_hf_cache()}: no download, so "
            "no heads-up") in out.splitlines()
    assert stub["argv"] == []


def test_all_cached_in_a_dry_run_says_so_too(stub, capsys):
    for r in WEIGHTS:
        _cached(r)
    assert services.install(["speech"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert "speech heads-up:" not in out and "all 3 already cached" in out


def test_no_prefetch_says_the_first_call_fetches_it(stub, capsys):
    assert services.install(["speech"], no_prefetch=True) == 0
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("speech heads-up:"))
    assert line.startswith("speech heads-up: the speech models are a 834 MB download, not fetched now "
                           "(--no-prefetch): the first call fetches it, and waits. The speech server "
                           "runs as a login service")
    assert stub["argv"] == []


def test_no_heads_up_when_install_does_not_install_the_speech_server(stub, capsys):
    _store(stub["home"])
    stub["state"]["probe"] = Probe(holder=VM, connects=True, identity="mlx-audio")
    assert services.install(["speech", "mouth", "keeper", "mail"]) == 0
    assert "heads-up" not in capsys.readouterr().out
    assert services.install(["mouth"]) == 0
    assert "heads-up" not in capsys.readouterr().out


def test_a_refused_install_gives_no_heads_up(stub, monkeypatch, capsys):
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _store(stub["home"])
    assert services.main(["install", "--only", "speech,mail"]) == 2
    assert "heads-up" not in capsys.readouterr().out


def test_the_total_is_summed_from_the_sizes():
    assert services.speech_weights_total() == sum(s for _, _, s, _, _ in services.SPEECH_WEIGHT_SIZES)


def test_the_total_follows_a_stub(monkeypatch):
    monkeypatch.setattr(services, "SPEECH_WEIGHT_SIZES", STUB)
    assert services.speech_weights_total() == 834_000_000


def test_the_heads_up_never_asks(stub, monkeypatch, capsys):
    """WARN, not ASK (findings/ design note): install reads nothing from stdin, so a scripted
    install, or an agent's tool shell with no terminal, never waits on a prompt."""
    import builtins
    monkeypatch.setattr(builtins, "input", lambda *a: pytest.fail("install asked"))
    import sys
    monkeypatch.setattr(sys, "stdin", None)
    assert services.install(["speech"]) == 0
    assert "speech heads-up:" in capsys.readouterr().out
