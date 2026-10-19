"""EARS-106: every turn the side-car named carries a `speaker`, not only a split turn.

EARS-14 wrote `speaker` only when it split a turn, and it split 0 of 719 live turns
(findings/root-cause.md): a one-speaker turn, nearly every turn, carried none, so
keeper/ears-mail.py mailed it as its default, theo@host1, whoever spoke. The capture now
names an unsplit turn from its own partials' labels by EARS-14's own rule
(:func:`ears.listen.speakers.turn_speaker`), marked ``speaker_from: labels``.

The label rows below are SYNTHETIC: each is shaped like a replayed turn's partials'
labels (how many partials, which were named, neutral or silent, and in what order), with
neutral names (alice, bob) for anyone but the mouth's seat and its owner. Text is
synthetic too (w1, w2, ...).
"""
from __future__ import annotations

from fnmatch import fnmatchcase

import pytest

from ears.listen.speakers import NEUTRAL, first_cut, turn_speaker
from tests.listen_helpers import read_lines
from tests.test_ears_mail import em_module
from tests.test_listen_speaker_split import TextLabels, run

M, C, B, O, S = "theo", "vera", "bob", "other", "silent"

# seq -> the turn's partials' labels, in order (ears on the MacBook Pro mic). The seqs
# name the replayed turns each row is shaped like (fix-002's read-only replay).
TURNS = {
    188962: [B],         # 1 partial: too short for K=2, named by its one label
    188929: [M, M, M],   # Theo
    188949: [C, C, C],   # Vera, on the speakers
    188972: [C, C, C],   # Vera
    188983: [C, C],      # Vera
    189003: [S],         # the TV, silent (mailed theo@host1, woke Vera)
    189011: [B, O, O, B],  # the TV, labelled bob
    # fix-002 (review flag F1): long, mostly-neutral turns with ONE named label. The old
    # any-length fallback named each theo; K or more partials and no K-holder is nobody.
    189151: [O, M, S, O, S],                            # the TV
    189201: [O, S, O, O, O, O, M, O, O, O, S, O, O],    # the TV
    151217: [M, S, O, O, S, O, O, O, S, O, O, S],
}
WANT = {188962: B, 188929: M, 188949: C, 188972: C, 188983: C, 189003: None, 189011: B,
        189151: None, 189201: None, 151217: None}
ONE_LABEL_LONG = (189151, 189201, 151217)

# seq 152251: Vera on the speakers, others talking, then Theo. EARS-14's replay cut it
# at partial 10, vera -> theo.
SPLIT_152251 = [C] * 6 + [O] * 4 + [M, S, M, M, S]


def by_text(labels):
    return {f"w{i + 1}": s for i, s in enumerate(labels)}


def words(a, b):
    return " ".join(f"w{i}" for i in range(a, b + 1))


# -- the rule ---------------------------------------------------------------------

@pytest.mark.parametrize("seq", sorted(TURNS))
def test_real_turns_are_named_by_their_partials(seq):
    assert turn_speaker(TURNS[seq]) == WANT[seq]


def test_the_tv_is_never_theo():
    assert turn_speaker(TURNS[189003]) != M and turn_speaker(TURNS[189011]) != M


@pytest.mark.parametrize("seq", ONE_LABEL_LONG)
def test_one_label_never_names_a_long_turn(seq):
    """fix-002, on review flag F1 (no split on a single
    label): the unanimous fallback is only for a turn of fewer than K
    partials. Each of these long turns holds one `theo` among neutrals: no speaker, so no
    explicit `speaker: theo` to wake `ears-speaker:theo@*` now the theo default is gone (EARS-110)."""
    labels = TURNS[seq]
    assert len(labels) >= 2 and labels.count(M) == 1
    assert turn_speaker(labels) is None


@pytest.mark.parametrize("labels,k,want", [
    ([M], 2, M),                       # fewer than K partials: one named label, nothing against it
    ([C], 2, C),
    ([None], 2, None), ([O], 2, None),  # 1 partial, unlabelled or neutral: nobody
    ([O, M, O], 2, None),              # K or more partials, nobody held K: one label never names it
    ([M, O], 2, None),                 # 2 partials at K=2 could have reached K: no fallback
    ([M, None], 2, None),
    ([M, None, M], 2, M),              # unlabelled partials are skipped, not a reset
    ([M, C], 2, None),                 # two names, neither held K: nobody
    ([M, O], 3, M),                    # K=3: 2 partials are too short to reach K, unanimous
    ([M, C], 3, None),                 # K=3, too short, two names: a conflict, nobody
    ([M, O, O], 3, None),              # K=3, 3 partials: no fallback
    ([M, C, M, C], 2, None),           # neither ever holds K: nobody
    (["alice", M, M, M], 2, M),        # a stray first label, as first_cut ignores it
    ([M, M, "alice", M, M], 2, M),     # a stray label mid-turn
    (SPLIT_152251, 2, None),           # a change not yet cut when the turn closed: nobody
    ([O, "mixed", S, "unheard"], 2, None),  # neutrals only: never a name
    ([None, None], 2, None), ([], 2, None),
    ([M, M], 0, None),                 # off
    ([M, M, C], 3, None),              # K=3: two names, none held K
    ([M, M, M, C], 3, M),
])
def test_turn_speaker(labels, k, want):
    assert turn_speaker(labels, k) == want


@pytest.mark.parametrize("label", sorted(NEUTRAL))
def test_a_neutral_label_never_names_a_turn(label):
    assert turn_speaker([label] * 5) is None


def test_first_cut_is_unchanged_on_the_real_split():
    """turn_speaker and first_cut share one scan (_holders); the split still cuts 152251
    exactly where EARS-14's replay did."""
    assert first_cut(SPLIT_152251) == (10, C, M)


# -- the capture --------------------------------------------------------------------

@pytest.mark.parametrize("seq", sorted(TURNS))
def test_the_capture_writes_each_real_turns_speaker(tmp_path, seq):
    labels = TURNS[seq]
    turns, lines, _ = run(tmp_path, len(labels), by_text(labels))
    assert [t["text"] for t in turns] == [words(1, len(labels))]       # one turn, never split
    t = turns[0]
    assert "split" not in t and "opened_by" not in t
    if WANT[seq] is None:
        assert "speaker" not in t and "speaker_from" not in t           # the TV names nobody
    else:
        assert t["speaker"] == WANT[seq] and t["speaker_from"] == "labels"
    assert not [ln for ln in lines if ln.get("event") == "speaker-split-error"]


def test_a_real_split_turn_still_splits_exactly_as_before(tmp_path):
    """152251 splits as EARS-14 split it: speaker, split / opened_by, and NO speaker_from on
    either half (a split's own speaker is never overwritten by the labels rule)."""
    turns, *_ = run(tmp_path, len(SPLIT_152251), by_text(SPLIT_152251))
    assert [(t["text"], t.get("speaker")) for t in turns] == [(words(1, 10), C), (words(11, 15), M)]
    assert turns[0]["split"] == "speaker-change" and "opened_by" not in turns[0]
    assert turns[1]["opened_by"] == "speaker-change" and "split" not in turns[1]
    assert all("speaker_from" not in t for t in turns)


def test_speaker_split_zero_is_todays_turns_exactly(tmp_path):
    """The rollback: --speaker-split 0 gives every line exactly as with no labels at all,
    and no turn carries a speaker, whatever the side-car says."""
    def strip(lines):
        return [{k: v for k, v in ln.items() if k not in ("ts", "wall_s")} for ln in lines]
    for seq, labels in TURNS.items():
        base = strip(run(tmp_path / f"{seq}-none", len(labels), k=0)[1])
        off_turns, off_lines, lab = run(tmp_path / f"{seq}-off", len(labels), by_text(labels), k=0)
        assert strip(off_lines) == base, seq
        assert all("speaker" not in t and "speaker_from" not in t for t in off_turns)
        assert lab.calls == 0                                       # the labels are not even read


def test_a_label_read_error_at_turn_close_never_breaks_the_capture(tmp_path):
    """One partial is never checked for a split (nothing to cut before it; EARS-360 lets
    two be, since the mouth's agent yields to ONE person partial), so the only label read
    is the turn's own, at its close: it raises, the turn is written as before (no
    speaker), said once, and the capture goes on to its end."""
    logs = tmp_path / "logs"
    lab = TextLabels(logs, {}, boom=True)
    turns, lines, _ = run(tmp_path, 1, labels=lab)
    assert [(t["text"], t.get("speaker")) for t in turns] == [("w1", None)]
    errs = [ln for ln in lines if ln.get("event") == "speaker-split-error"]
    assert len(errs) == 1 and "garbage" in errs[0]["detail"] and lab.calls == 1
    stopped = [ln for ln in read_lines(logs) if ln.get("event") == "listen-stopped"]
    assert stopped and stopped[-1]["reason"] == "eof" and stopped[-1]["turns"] == 1


# -- what ears-mail mints from it (super.ears note 2) --------------------------------

DEFAULT = "unknown@host1"  # ears-mail's default Ears-Speaker on host1 with no --speaker (EARS-110)
WAKE_GLOB = "theo@*"  # the wake rule `ears-speaker:theo@*`, matched as the pager does:
                      # fnmatchcase on the folded (lower-cased) header value


def wakes(header):
    return fnmatchcase(header.lower(), WAKE_GLOB)


@pytest.mark.parametrize("label,minted", [
    ("vera", "vera@host1"), ("alice", "alice@host1"), ("bob", "bob@host1"), ("theo", "theo@host1"),
    # a neutral label, or none, mints ears-mail's DEFAULT (--speaker / $EARS_SPEAKER /
    # unknown@host1): unknown everywhere (EARS-110)
    ("mixed", "unknown@host1"), ("other", "unknown@host1"), ("silent", "unknown@host1"),
    ("unheard", "unknown@host1"), (None, "unknown@host1"),
])
def test_speaker_of_pinned_per_label(label, minted):
    rec = {} if label is None else {"speaker": label}
    assert em_module().speaker_of(rec, DEFAULT, "host1") == minted


def test_only_a_theo_turn_matches_the_theo_wake_glob():
    """vera@host1 (and alice/bob) never match `ears-speaker:theo@*`: Vera's own lines stop paging
    her. The capture never writes a neutral label as `speaker`, so a TV turn reaches
    ears-mail with none and mints the default, unknown@host1 since EARS-110,
    which does NOT match: the TV no longer wakes anyone as Theo."""
    em = em_module()
    mint = lambda labels: em.speaker_of(  # noqa: E731
        {"speaker": s} if (s := turn_speaker(labels)) else {}, DEFAULT, "host1")
    assert [wakes(h) for h in ("vera@host1", "alice@host1", "bob@host1", "Vera@host1")] == [False] * 4
    assert wakes("theo@host1") and wakes("Theo@HOST1")
    assert not wakes(mint(TURNS[188949])) and not wakes(mint(TURNS[188983]))   # Vera's lines
    assert wakes(mint(TURNS[188929]))                                          # Theo's line
    assert not wakes(mint(TURNS[189011]))                                      # TV, labelled bob
    assert mint(TURNS[189003]) == DEFAULT and not wakes(mint(TURNS[189003]))    # TV, silent: default
    for neutral in ("mixed", "other", "silent", "unheard"):
        assert not wakes(em.speaker_of({"speaker": neutral}, DEFAULT, "host1"))  # the default, as pinned
