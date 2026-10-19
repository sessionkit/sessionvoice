"""The suite-wide send fence (EARS-164 T4, EARS-381): outside tests/mouth, HOME is
not fenced, so a drop notice must not be able to mail anyone. tests/conftest.py
points $MOUTH_SENDMAIL (the command notices go through, now with `message send`'s
arguments) at a stub that ignores its argv, writes nothing and exits 75."""

import os
import subprocess
import sys
from pathlib import Path

REFUSE = Path(__file__).parent / "fixtures" / "refuse_sendmail.py"


def test_suite_default_sendmail_refuses(tmp_path):
    assert os.environ["MOUTH_SENDMAIL"].endswith(REFUSE.name)
    p = subprocess.run([sys.executable, str(REFUSE), "send", "ivy@x", "-s", "x", "-f", "-",
                        "--json"],
                       input=b"x\n", cwd=tmp_path, capture_output=True, timeout=10, check=False)
    assert p.returncode == 75 and list(tmp_path.iterdir()) == []


def test_a_notice_outside_the_mouth_fence_is_recorded_failed():
    from mouth import notice

    item = {"utt": "u1", "text": "x", "notice_to": "ivy@" + notice._host()}
    assert notice.dropped(item, {"reason": "error", "cut": True}) == "failed: message send exit 75"
