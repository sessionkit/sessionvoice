"""The capture follows ears.json's ``stt`` live (FollowingSTT): a change of engine lands on
the next chunk, with no restart. A flag or
EARS_LISTEN_STT_URL/_MODEL pins the engine instead, as before."""
import json
import os
import threading

import numpy as np
import pytest

from ears import envnames
from ears.listen import __main__ as listen_main
from ears.listen import stt

AUDIO = np.zeros(1600, dtype=np.int16)


class Fake:
    name = "whisper"

    def __init__(self, url, model):
        self.url, self.model, self.closed = url, model, False

    def transcribe(self, audio):
        return f"{self.url} {self.model}"

    def close(self):
        self.closed = True


@pytest.fixture
def conf(tmp_path, monkeypatch):
    path = tmp_path / "ears.json"
    monkeypatch.setenv("EARS_CONF", str(path))
    n = [0]

    def write(text):
        path.write_text(text if isinstance(text, str) else json.dumps(text))
        n[0] += 1   # a distinct mtime each write: a coarse clock must not hide a change
        os.utime(path, ns=(10**18 + n[0] * 10**9, 10**18 + n[0] * 10**9))

    write.path = path
    return write


def _following(log=None):
    made = []

    def make(url, model):
        made.append(Fake(url, model))
        return made[-1]

    return stt.FollowingSTT(make=make, log=(log.append if log is not None else lambda m: None)), made


def test_a_change_lands_on_the_next_call_without_closing_the_old(conf):
    conf({"stt": {"url": "http://a:1/v1", "model": "apple"}})
    log = []
    s, made = _following(log)
    assert s.transcribe(AUDIO) == "http://a:1/v1 apple"
    conf({"stt": {"url": "http://p:8890/v1/", "model": "parakeet"}, "listen": True})
    assert s.transcribe(AUDIO) == "http://p:8890/v1 parakeet"
    assert (s.base_url, s.model, s.name) == ("http://p:8890/v1", "parakeet", "whisper")
    assert len(made) == 2 and not made[0].closed      # in flight on an executor, maybe
    assert log == ["ears: stt http://a:1/v1 apple -> http://p:8890/v1 parakeet (ears.json)"]
    s.close()
    assert all(c.closed for c in made)


def test_an_unchanged_file_builds_nothing(conf):
    conf({"stt": {"url": "http://a:1/v1", "model": "m"}})
    s, made = _following()
    conf({"stt": {"url": "http://a:1/v1", "model": "m"}, "input": "airpods"})   # another key changed
    for _ in range(3):
        s.transcribe(AUDIO)
    assert len(made) == 1


def test_a_file_caught_mid_write_keeps_the_engine_and_is_read_again(conf):
    conf({"stt": {"url": "http://a:1/v1", "model": "m"}})
    s, made = _following()
    conf('{"stt": {"url": "http://p:2')
    assert s.transcribe(AUDIO) == "http://a:1/v1 m"
    assert s.transcribe(AUDIO) == "http://a:1/v1 m"
    conf({"stt": {"url": "http://p:2/v1", "model": "m"}})
    assert s.transcribe(AUDIO) == "http://p:2/v1 m" and len(made) == 2


def test_no_file_and_no_stt_mean_the_fallback(conf):
    s, made = _following()
    assert (s.base_url, s.model) == (stt.FALLBACK_URL, stt.FALLBACK_MODEL)
    conf({"stt": {"url": "http://p:2/v1"}})
    assert s.transcribe(AUDIO) == f"http://p:2/v1 {stt.URL_ONLY_MODEL}"   # not Parakeet's name
    conf({"listen": True})
    assert s.transcribe(AUDIO) == f"{stt.FALLBACK_URL} {stt.FALLBACK_MODEL}"
    os.remove(conf.path)
    assert s.transcribe(AUDIO) == f"{stt.FALLBACK_URL} {stt.FALLBACK_MODEL}" and len(made) == 3


def test_threads_racing_a_change_build_one_client(conf):
    conf({"stt": {"url": "http://a:1/v1"}})
    s, made = _following()
    conf({"stt": {"url": "http://b:1/v1"}})
    go = threading.Barrier(8)

    def call():
        go.wait()
        s.transcribe(AUDIO)

    ts = [threading.Thread(target=call) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert [c.url for c in made] == ["http://a:1/v1", "http://b:1/v1"]


def test_the_default_client_is_a_whisper_on_the_file(conf):
    conf({"stt": {"url": "http://p:8890/v1", "model": "parakeet"}})
    s = stt.FollowingSTT(drop_annotations=False)
    try:
        assert isinstance(s.current(), stt.WhisperSTT)
        assert (s.current().base_url, s.current().model, s.current().drop_annotations) == ("http://p:8890/v1", "parakeet", False)
    finally:
        s.close()


@pytest.mark.parametrize("argv, env, follows", [
    ([], {}, True),
    (["--stt-url", "http://x:1/v1"], {}, False),
    (["--stt-model", "m"], {}, False),
    ([], {"EARS_LISTEN_STT_URL": "http://e:1/v1"}, False),
    ([], {"EARS_LISTEN_STT_MODEL": "m"}, False),
])
def test_capture_follows_unless_pinned(conf, monkeypatch, argv, env, follows):
    conf({"stt": {"url": "http://p:8890/v1", "model": "parakeet"}})
    for k in (*envnames.group("EARS_LISTEN_STT_URL"), *envnames.group("EARS_LISTEN_STT_MODEL")):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    args = listen_main.build_parser().parse_args(["capture", *argv])
    s = listen_main.make_stt(args)
    try:
        assert isinstance(s, stt.FollowingSTT) is follows
        if not follows:
            assert isinstance(s, stt.WhisperSTT)
            if "--stt-url" in argv:
                assert s.base_url == "http://x:1/v1"
    finally:
        s.close()


def test_the_keeper_no_longer_restarts_the_capture_on_an_engine_change():
    from pathlib import Path
    keeper = (Path(__file__).resolve().parents[1] / "keeper" / "ears-keeper.sh").read_text()
    block = keeper[keeper.index('st="$(stt_conf)"'):]
    block = block[:block.index('if [ "$FOLLOW" = 1 ] && [ "$t" != "$last_t" ]')]
    assert "stop_capture" not in block and "capture follows" in block


def test_final_is_read_before_its_old_name_stt(conf):
    conf({"stt": {"url": "http://old:1/v1", "model": "o"}, "final": {"url": "http://new:1/v1", "model": "n"}})
    s, made = _following()
    assert s.transcribe(AUDIO) == "http://new:1/v1 n"
    assert stt.conf_stt() == {"url": "http://new:1/v1", "model": "n"}
    conf({"stt": {"url": "http://old:1/v1", "model": "o"}})          # only the old name: still read
    assert s.transcribe(AUDIO) == "http://old:1/v1 o"


def test_partials_setting_defaults_to_final(conf):
    conf({})
    assert stt.conf_partials() == "final"
    conf({"partials": "nemotron"})
    assert stt.conf_partials() == "nemotron"
    conf({"partials": "parakeet-ish"})                                  # not a choice: the default
    assert stt.conf_partials() == "final"
