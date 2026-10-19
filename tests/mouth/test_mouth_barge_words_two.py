"""Two content words cut a line, not three (EARS-274, option a).

STOP_WORDS is kept, BARGE_WORDS goes 3 -> 2, and
nothing else changes. These tests replay the rows the change was made on,
copied in as literals from the EARS-274 task store (``findings/``, read-only):

- his two barge-ins on AirPods on 2030-06-09 (the heard-log
  rows, the word-path findings; the line texts,
  ``findings/2030-06-09-line-texts.md``; ``findings/replay274.py``). Live, at
  3 words, the cut came on the 7th and 6th partial, 5.28 s and 2.48 s after his
  voice onset. At 2 words it comes on "barge in now" and "words and": 3.04 s
  and 1.38 s.
- backchannels from another host's lines that the 2-word rule still does NOT cut (hand
  verdict B in ``findings/replay274_handread.tsv``: the line should have
  played on). Options without the stop list cut them; option (a) keeps it.

Each partial is written to a scratch heard log in order and the real
``hold.barge()`` is asked after each one, at that partial's own time.
"""
from __future__ import annotations

import json
from datetime import datetime
from itertools import count

import pytest

from ears import heard
from mouth import hold
from tests import env_fence


@pytest.fixture
def heard_log(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    monkeypatch.setenv("MOUTH_BARGE", "1")
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS_CALL")
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "no-mouth.json"))
    heard.log_dir().mkdir(parents=True, exist_ok=True)
    seqs = count(448237)

    def write(t, text):
        ts = datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")
        with open(heard.log_path(), "a") as f:
            f.write(json.dumps({"ts": ts, "seq": next(seqs), "kind": "partial",
                                "text": text, "source": "mic"}) + "\n")
        for c in hold._caches.values():
            c.update(t=0.0, v=None)
    return write


def replay(write, line, saying_s, partials, words=None):
    """Write ``partials`` ((seconds, text), on the same clock as ``saying_s``,
    the line's first frame) one by one, asking ``hold.barge`` after each.
    Returns (seconds, text) of the partial it cut on, or None."""
    base = datetime.now().timestamp() - 120.0 - saying_s     # wall time of second 0
    since = base + saying_s
    for s, text in partials:
        write(base + s, text)
        got = hold.barge(since, line, now=base + s + 0.05, words=words)
        if got is not None:
            return s, got["text"]
    return None


# -- the two 08:00 lines (seconds past 08:00:00; line 2 runs past 08:01) --

LINE1 = ("Go for it. I'll keep talking for a while so you've got something to cut "
         "into. Here's some filler: the little man the magpie came by today, or "
         "maybe he didn't, I wouldn't know, I'm on a laptop under a table at eight "
         "in the morning, and nobody tells me anything about birds. Kuka "
         "the kookaburra would have something to say about that. Anyway, any time "
         "now, just start talking over me and see how fast I duck.")
LINE2 = LINE1[LINE1.index("Here's"):]                # the re-offer, b1a18dd2d76c
SAYING1, ONSET1 = 36.478, 39.186                     # 42ec8fb04957, seq 448236 / vad 448237
PARTIALS1 = [(40.606, "Okay well"), (41.147, "I'm just trying"), (41.686, "to"),
             (42.226, "barge in now"), (42.786, "so we'll see"), (43.367, "what"),
             (44.467, "happens and I'm")]
SAYING2, ONSET2 = 57.289, 60.890                     # b1a18dd2d76c, seq 448256 / vad 448258
PARTIALS2 = [(61.186, "Okay, so now"), (61.246, "I'm"), (61.666, "saying some more"),
             (62.266, "words and"), (62.806, "I'm"), (63.366, "speaking")]


def test_the_default_is_two_words(heard_log, monkeypatch):
    assert hold.BARGE_WORDS == 2
    assert hold.barge_words() == 2 and hold.barge_words("airpods") == 2
    assert hold.barge_words(None, {}) == 2                   # nothing in the settings
    assert hold.barge_words("call:1317") == 2 and hold.barge_words("call") == 2   # one policy (EARS-315)
    monkeypatch.setenv("MOUTH_BARGE_WORDS", "3")
    assert hold.barge_words("airpods", {}) == 3              # the env: the default's layer
    assert hold.barge_words(None, {"barge_words": 4}) == 4   # the user's setting beats it (EARS-315)
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    assert hold.barge_words(None, {"barge_words": 4}) == 4   # the setting, alone


@pytest.mark.parametrize("line, saying, onset, partials, cut_on, onset_to_cut", [
    (LINE1, SAYING1, ONSET1, PARTIALS1, (42.226, "barge in now"), 3.04),
    (LINE2, SAYING2, ONSET2, PARTIALS2, (62.266, "words and"), 1.38),
], ids=["line1-42ec8fb04957", "line2-b1a18dd2d76c"])
def test_his_0800_barge_ins_cut_on_the_second_word(heard_log, line, saying, onset,
                                                   partials, cut_on, onset_to_cut):
    got = replay(heard_log, line, saying, partials)      # the default: words=None
    assert got == cut_on
    assert round(got[0] - onset, 2) == onset_to_cut


@pytest.mark.parametrize("line, saying, onset, partials, live_cut, live_onset_to_cut", [
    (LINE1, SAYING1, ONSET1, PARTIALS1, (44.467, "happens and I'm"), 5.28),
    (LINE2, SAYING2, ONSET2, PARTIALS2, (63.366, "speaking"), 2.48),
], ids=["line1-42ec8fb04957", "line2-b1a18dd2d76c"])
def test_at_three_words_the_same_rows_cut_where_they_did_live(heard_log, line, saying,
                                                             onset, partials, live_cut,
                                                             live_onset_to_cut):
    """The replay is faithful: at 3 words it lands on the partial the live cut
    followed (44.467 -> said 44.649; 03.366 -> said 03.551)."""
    got = replay(heard_log, line, saying, partials, words=3)
    assert got == live_cut
    assert round(got[0] - onset, 2) == live_onset_to_cut


# -- backchannels that two words still do not cut (hand verdict B) ------------

BACKCHANNELS = [
    # utt, the line playing, his partials over it
    ("ec8ba85b5bdc", "Measured: the voice work dropped from about sixty percent CPU to under ten.",
     ["Okay, that's great."]),
    ("5056242ba0e9", "Doing it. We'd done it for my seat only, not for everyone.",
     ["Yes, please."]),
    ("b27f179a5131", "In the prep run, before you rename. I'd call the three steps the prep, "
     "the break, and the restore. Prep: the mail guard, the memory port, and Ivy's doorbell "
     "fix. The launcher waits for the restore. So yes: I'll plan the prep now, in my boot "
     "letter, then settle, and the new me runs it.",
     ["Yes, please. Yeah."]),
    ("22d95855d696", "I've hit my handover mark. Settling now; a fresh me in a few minutes, "
     "same conversation.",
     ["Thank you.", "Um,"]),
    ("42ec8fb04957 first partial", LINE1, ["Okay well"]),
]


@pytest.mark.parametrize("utt, line, partials", BACKCHANNELS, ids=[b[0] for b in BACKCHANNELS])
def test_a_backchannel_still_does_not_cut(heard_log, utt, line, partials):
    rows = [(1.0 + 0.6 * i, text) for i, text in enumerate(partials)]
    assert replay(heard_log, line, 0.0, rows) is None


def test_one_content_word_is_not_enough(heard_log):
    """"Yes, please." has one content word: still one short of two."""
    assert replay(heard_log, "Doing it.", 0.0, [(1.0, "Yes, please.")]) is None
    assert replay(heard_log, "Doing it.", 0.0, [(2.0, "Yes, please, carry on.")]) \
        == (2.0, "Yes, please, carry on.")
