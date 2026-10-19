"""EARS-294 T5: inside a mouth test, the per-test fence (tests/mouth/conftest.py, through
tests/env_fence.py's MOUTH_TEST_FENCE) holds under EVERY name of each variable, so a runner
that exports the old VOICEMODE_MOUTH_ name, or the new MOUTH_ one, cannot reach a test."""
import os

import pytest

from ears import envnames
from tests import env_fence


@pytest.mark.parametrize("name", sorted(env_fence.MOUTH_TEST_FENCE))
def test_the_mouth_fence_is_on_under_every_name_now(name, heard_box):
    names = envnames.group(name)
    if env_fence.MOUTH_TEST_FENCE[name] is None:
        assert not [n for n in names if n in os.environ]
    else:
        values = {os.environ.get(n) for n in names}
        assert len(values) == 1 and None not in values and "" not in values, (name, values)
    if name == "MOUTH_HEARD_BOX":
        assert all(os.environ[n] == str(heard_box) for n in names)


def test_the_fake_sendmail_is_the_send_command_under_both_names():
    for n in envnames.group("MOUTH_SENDMAIL"):
        assert os.environ[n].endswith("fake_sendmail.py"), n
