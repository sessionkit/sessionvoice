"""ears services install: bootstrap waits for the old job, and retries once (error 5)."""
import subprocess
from ears import services


def test_bootstrap_waits_for_the_old_job_then_retries(monkeypatch, tmp_path):
    calls, prints = [], iter([0, 0, 113])           # the old job lingers for two looks, then is gone
    boots = iter([5, 0])                             # first bootstrap hits the window, second loads
    def fake(*a):
        calls.append(a[0])
        rc = next(prints) if a[0] == "print" else next(boots)
        return subprocess.CompletedProcess(a, rc, "", "5: Input/output error" if rc == 5 else "")
    monkeypatch.setattr(services, "_launchctl", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    r = services._bootstrap(501, "room", tmp_path / "x.plist")
    assert r.returncode == 0
    assert calls == ["print", "print", "print", "bootstrap", "bootstrap"]
