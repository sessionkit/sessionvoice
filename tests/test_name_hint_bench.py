"""EARS-246 do-003: ``bench/name_hint.py`` measures the STT name hint, offline here.

A fake ``say`` (a tone per line) and a fake STT (which looks each clip up by its
sha256 and answers as a mishearing whisper would) drive the whole bench: the corpus
is reproducible, the conditions degrade as they say, every transcript is scored by
ears-mail's own ``_call`` regex from the PRODUCTION names, hint ON sends the prompt
``ears.wake`` builds, and a prompt echo on silence is reported as a false wake.
One test drives the REAL ``WhisperSTT`` over a mock transport, to pin that the bench's
client sends the prompt field. No ``say``, no server, no network.
"""
from __future__ import annotations

import hashlib
import json
import zlib

import httpx
import numpy as np
import pytest

from bench import name_hint as nh
from ears import envnames

SR = nh.SR


def small_manifest():
    m = nh.load_manifest()
    m["voices"] = ["Alpha", "Beta"]
    m["rates"] = [180]
    m["positives"]["tails"] = ["what's the time"]
    m["negatives"]["speech"] = ["turn the music down a little"]
    m["negatives"]["near"] = ["hey Davis, what's the time", "hey caves are cold inside"]
    return m


def fake_say(calls=None, missing=()):
    def synth(text, voice, rate):
        if calls is not None:
            calls.append((text, voice, rate))
        if voice in missing:
            raise nh.BenchError(f"say -v {voice}: Voice `{voice}' not found.")
        f = 200 + zlib.crc32(f"{text}|{voice}|{rate}".encode()) % 1500
        t = np.arange(int(0.6 * SR)) / SR
        return (0.3 * 32767 * np.sin(2 * np.pi * f * t)).astype(np.int16)
    return synth


class MishearingSTT:
    """Answers per clip, found by the audio's sha256: hint OFF hears Dave as "Davis"
    (the bug); hint ON hears it right. Bob is heard either way. Silence under the hint
    comes back as a call (a prompt echo that wakes). Records every prompt it was given."""

    def __init__(self, by_sha, prompt, seen):
        self.by_sha, self.prompt, self.seen = by_sha, prompt, seen

    def transcribe(self, audio):
        p = self.prompt() if callable(self.prompt) else self.prompt
        self.seen.append(p)
        c = self.by_sha[hashlib.sha256(np.asarray(audio, dtype=np.int16).tobytes()).hexdigest()]
        if c["kind"] == "pos":
            if c["seat"] == "dave":
                return "Hey, Dave, what's the time?" if p else "Hey Davis, what's the time?"
            if c["seat"] == "carol":
                return "Hey, Carol, what's the time?" if p else "Hey Carl, what's the time?"
            return "Hey Bob, what's the time?"
        if c["kind"] == "silence":
            # an echo that wakes: the bare-list prompt read back holds no "hey" and would
            # not, so the fake models the "Hey X." shape's echo to keep the bench's flag tested
            return "Hey Dave." if p else ""
        return c["text"]


@pytest.fixture
def corpus(tmp_path):
    m = small_manifest()
    clips = nh.build(m, tmp_path, synth=fake_say(), log=lambda s: None)
    return m, tmp_path, clips


def fake_factory(clips, seen):
    by_sha = {c["sha256"]: c for c in clips}
    return lambda url, prompt: MishearingSTT(by_sha, prompt, seen)


# -- the corpus -------------------------------------------------------------------------

def test_every_utterance_is_rendered_through_every_condition(corpus):
    m, root, clips = corpus
    us = nh.utterances(m)
    assert len(clips) == len(us) * len(m["conditions"])
    assert len({c["id"] for c in clips}) == len(clips)
    kinds = {c["kind"] for c in clips}
    assert kinds == {"pos", "speech", "near", "silence", "noise"}
    assert {c["seat"] for c in clips if c["kind"] == "pos"} == set(m["wake"]["seats"])
    # the seat is spoken as the manifest's say form, behind the opener
    assert any(c["text"] == "hey Dave, what's the time" for c in clips)
    for c in clips[:: max(1, len(clips) // 12)]:
        x = nh.read_wav(root / c["wav"])
        assert x.dtype == np.int16 and len(x) == c["samples"]
    import wave
    with wave.open(str(root / clips[0]["wav"])) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
    head, rows = nh.load_index(root)
    assert head["manifest"] == nh.manifest_hash(m) and len(rows) == len(clips)
    assert json.loads((root / "wake.json").read_text()) == m["wake"]


def test_the_corpus_is_reproducible_and_a_same_manifest_build_is_reused(tmp_path):
    m = small_manifest()
    a = nh.build(m, tmp_path / "a", synth=fake_say(), log=lambda s: None)
    b = nh.build(m, tmp_path / "b", synth=fake_say(), log=lambda s: None)
    assert [c["sha256"] for c in a] == [c["sha256"] for c in b]
    calls = []
    again = nh.build(m, tmp_path / "a", synth=fake_say(calls), log=lambda s: None)
    assert calls == [] and again == a
    nh.build(m, tmp_path / "a", synth=fake_say(calls), rebuild=True, log=lambda s: None)
    assert calls
    m2 = small_manifest()
    m2["seed"] = 7                                 # a changed manifest is rebuilt
    calls.clear()
    nh.build(m2, tmp_path / "a", synth=fake_say(calls), log=lambda s: None)
    assert calls


def test_a_voice_say_lacks_is_skipped_and_named(tmp_path):
    m = small_manifest()
    said = []
    clips = nh.build(m, tmp_path, synth=fake_say(missing={"Beta"}), log=said.append)
    assert not any(c["voice"] == "Beta" for c in clips)
    assert any(c["voice"] == "Alpha" for c in clips)
    assert nh.load_index(tmp_path)[0]["skipped_voices"] == ["Beta"]
    assert any("skip voice Beta" in s for s in said)


# -- the conditions ---------------------------------------------------------------------

def _band_energy(x, lo, hi):
    X = np.abs(np.fft.rfft(np.asarray(x, dtype=np.float64)))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float(np.sum(X[(f >= lo) & (f <= hi)] ** 2))


def _tones(*freqs, s=1.0):
    t = np.arange(int(s * SR)) / SR
    return sum(0.2 * np.sin(2 * np.pi * f * t) for f in freqs)


def test_the_phone_band_drops_what_a_phone_cannot_carry():
    m = nh.load_manifest()
    x = _tones(1000, 6000, 100)
    y = nh.degrade(x, m["conditions"]["phone8k"], np.random.default_rng(0))
    assert y.dtype == np.int16 and len(y) == len(x)
    keep = _band_energy(y, 900, 1100)
    assert _band_energy(y, 5900, 6100) < keep * 1e-3      # over 4 kHz: gone at 8 kHz
    assert _band_energy(y, 90, 110) < keep * 1e-2         # under 300 Hz: band-passed out
    assert keep > _band_energy(nh.degrade(x, m["conditions"]["clean"], None), 900, 1100) * 0.5


def test_bluetooth_band_keeps_speech_and_the_noise_condition_adds_noise():
    m = nh.load_manifest()
    x = _tones(1000, 7800)
    bt = nh.degrade(x, m["conditions"]["bt16k"], np.random.default_rng(0))
    assert _band_energy(bt, 7700, 7900) < _band_energy(bt, 900, 1100) * 1e-3
    noisy = nh.degrade(x, m["conditions"]["bt16k-noise"], np.random.default_rng(0))
    assert nh._rms(noisy.astype(float) - bt.astype(float)) > 0
    # silence under the noise condition is noise at the floor, not silence
    quiet = nh.degrade(np.zeros(SR), m["conditions"]["bt16k-noise"], np.random.default_rng(0))
    assert 0 < nh._rms(quiet / 32767) < nh._db(-35)
    assert not nh.degrade(np.zeros(SR), m["conditions"]["clean"], None).any()


# -- the URL guard ------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://127.0.0.1:2122/v1", "http://localhost:2122/v1",
                                 "http://[::1]:2122/v1", "http://127.0.0.2:9000"])
def test_a_loopback_url_off_2022_is_allowed(url):
    assert nh.check_url(url) == url


@pytest.mark.parametrize("url,why", [("http://host1.local:2122/v1", "not loopback"),
                                     ("http://192.0.2.5:2122/v1", "not loopback"),
                                     ("http://127.0.0.1:2022/v1", "2022"),
                                     ("ftp://127.0.0.1/x", "http(s)")])
def test_a_remote_or_live_url_is_refused(url, why):
    with pytest.raises(nh.BenchError, match=why.replace("(", r"\(").replace(")", r"\)")):
        nh.check_url(url)


def test_the_overrides_are_explicit():
    assert nh.check_url("http://host1.local:2122/v1", allow_remote=True)
    assert nh.check_url("http://127.0.0.1:2022/v1", allow_2022=True)
    with pytest.raises(nh.BenchError):            # remote AND 2022 needs both
        nh.check_url("http://host1.local:2022/v1", allow_remote=True)


def test_main_refuses_a_remote_url_before_building_anything(tmp_path, capsys):
    calls = []
    rc = nh.main(["--root", str(tmp_path), "run", "--url", "http://host1.local:2022/v1"],
                 synth=fake_say(calls))
    assert rc == 2 and calls == [] and not (tmp_path / "corpus").exists()
    assert "not loopback" in capsys.readouterr().err


# -- scoring: ears-mail's own regex, the production names ---------------------------------

def test_scoring_is_ears_mails_call_regex_on_the_production_names(corpus):
    m, root, clips = corpus
    rules = nh.load_rules(root / "wake.json")
    pos = {"kind": "pos", "seat": "dave"}
    assert nh.score(pos, "Hey, Dav.", rules)["hit"]          # a sound-alike in its names
    assert nh.score(pos, "hay daev what's up", rules)["hit"]
    assert not nh.score(pos, "Hey Davis, what's the time?", rules)["hit"]
    wrong = nh.score(pos, "Hey Bob, are you there", rules)
    assert wrong["wrong"] and wrong["called"] == ["bob"]
    neg = {"kind": "near", "seat": None}
    assert not nh.score(neg, "Hey Davis, what's the time", rules)["false_wake"]
    assert nh.score(neg, "Hey Dave.", rules)["false_wake"]
    # the same regex ears-mail decides a wake with
    mod = nh.ears_mail()
    assert mod.load_rules(str(root / "wake.json"), [])["dave"]["_call"].pattern == rules["dave"]["_call"].pattern


# -- run and report -----------------------------------------------------------------------

def test_hint_off_and_on_are_measured_and_the_prompt_is_the_wake_files(corpus):
    m, root, clips = corpus
    seen = []
    res = nh.run(clips, root, root / "wake.json", "http://127.0.0.1:2122/v1",
                 make_stt=fake_factory(clips, seen), log=lambda s: None)
    assert len(res) == 2 * len(clips)
    assert set(seen) == {None, "Dave, Bob, Carol."}    # ears.wake's prompt, keys only
    rows = {(r["condition"], r["mode"]): r for r in nh.summarize(res)}
    off, on = rows[("ALL", "off")], rows[("ALL", "on")]
    npos = sum(1 for c in clips if c["kind"] == "pos")
    assert off["pos"] == on["pos"] == npos
    assert off["hits"] == npos // 3                 # only bob: dave and carol misheard
    assert on["hits"] == npos
    # near-words stay negative; silence under the hint echoes the prompt: a false wake
    nsil = sum(1 for c in clips if c["kind"] == "silence")
    assert off["false_wakes"] == 0
    assert on["false_wakes"] == on["nonspeech_wakes"] == nsil
    for cond in m["conditions"]:
        assert rows[(cond, "on")]["pos"] == npos // len(m["conditions"])
    text = nh.report(res, "http://127.0.0.1:2122/v1")
    assert "WARNING" in text and "prompt echo" in text
    assert "'Dave, Bob, Carol.'" in text
    assert "per seat, hint off: dave 0/" in text
    assert "NOTE" not in text


def test_a_baseline_that_already_hits_says_the_corpus_proves_nothing(corpus):
    m, root, clips = corpus

    class Perfect:
        def __init__(self, by_sha):
            self.by_sha = by_sha

        def transcribe(self, audio):
            c = self.by_sha[hashlib.sha256(np.asarray(audio, dtype=np.int16).tobytes()).hexdigest()]
            return f"Hey {c['seat']}, hi" if c["kind"] == "pos" else ""

    by = {c["sha256"]: c for c in clips}
    res = nh.run(clips, root, root / "wake.json", "http://127.0.0.1:2122/v1",
                 make_stt=lambda url, p: Perfect(by), log=lambda s: None)
    text = nh.report(res)
    assert "NOTE" in text and "NOT reproduced" in text and "WARNING" not in text


def test_hint_text_overrides_the_prompt_verbatim(corpus):
    m, root, clips = corpus
    seen = []
    nh.run(clips[:4], root, root / "wake.json", "http://127.0.0.1:2122/v1", modes=("on",),
           hint_text="Hey Dave.", make_stt=fake_factory(clips, seen), log=lambda s: None)
    assert set(seen) == {"Hey Dave."}


def test_a_failing_stt_is_recorded_and_a_dead_one_aborts(corpus):
    m, root, clips = corpus

    class Flaky:
        n = 0

        def transcribe(self, audio):
            Flaky.n += 1
            if Flaky.n % 2:
                raise RuntimeError("boom")
            return ""

    res = nh.run(clips[:6], root, root / "wake.json", "u", modes=("off",),
                 make_stt=lambda url, p: Flaky(), log=lambda s: None, give_up=5)
    assert sum(1 for r in res if r["error"]) == 3
    assert nh.summarize(res)[-1]["errors"] == 3

    class Dead:
        def transcribe(self, audio):
            raise RuntimeError("connection refused")

    with pytest.raises(nh.BenchError, match="all failed"):
        nh.run(clips, root, root / "wake.json", "u", make_stt=lambda url, p: Dead(),
               log=lambda s: None, give_up=5)


def test_main_runs_end_to_end_and_writes_the_run(tmp_path, capsys, monkeypatch):
    m = small_manifest()
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(m))
    root = tmp_path / "harness"
    clips = nh.build(m, root, synth=fake_say(), log=lambda s: None)
    seen = []
    rc = nh.main(["--manifest", str(mf), "--root", str(root), "run", "--url", "http://127.0.0.1:2122/v1"],
                 synth=fake_say(), make_stt=fake_factory(clips, seen))
    assert rc == 0
    out = capsys.readouterr().out
    assert "name_hint bench" in out and "ALL" in out
    (run,) = (root / "runs").iterdir()
    assert (run / "report.txt").read_text().strip() == out.strip()
    rows = [json.loads(s) for s in (run / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 2 * len(clips)
    assert nh.main(["--manifest", str(mf), "--root", str(root), "run", "--url",
                    "http://127.0.0.1:2122/v1", "--modes", "sideways"], synth=fake_say()) == 2


def test_the_default_harness_is_under_ears_eval_harness(monkeypatch, tmp_path):
    monkeypatch.setenv("EARS_EVAL_HARNESS", str(tmp_path))
    assert nh.harness_root() == tmp_path / "name-hint"


# -- the REAL client sends the prompt --------------------------------------------------------

def test_the_bench_drives_the_real_whisper_client_with_the_prompt(corpus, monkeypatch):
    m, root, clips = corpus
    for k in envnames.group("EARS_LISTEN_STT_KEY"):
        monkeypatch.delenv(k, raising=False)
    sent = []

    def handler(request):
        body = request.content.decode("latin-1")
        sent.append("Dave, Bob, Carol." in body and 'name="prompt"' in body)
        return httpx.Response(200, json={"text": "Hey Dave, what's the time?"})

    def real(url, prompt):
        stt = nh.whisper_stt(url, prompt)
        stt._client = httpx.Client(transport=httpx.MockTransport(handler))
        return stt

    pos = [c for c in clips if c["kind"] == "pos" and c["seat"] == "dave"][:2]
    res = nh.run(pos, root, root / "wake.json", "http://127.0.0.1:2122/v1", make_stt=real,
                 log=lambda s: None)
    assert sent == [False, False, True, True]       # off: no prompt field; on: the wake file's
    assert all(r["hit"] for r in res)
