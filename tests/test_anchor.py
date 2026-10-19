"""EARS-397: the test-time anchor holds what its definition promises."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests._anchor import ANCHOR, ANCHOR_ISO, ANCHOR_SUMMER, ANCHOR_SUMMER_ISO

SYDNEY = ZoneInfo("Australia/Sydney")


def test_the_anchor_is_a_sunday_in_2030_without_daylight_saving():
    local = ANCHOR.astimezone(SYDNEY)
    assert (ANCHOR.year, ANCHOR.strftime("%a"), local.strftime("%a")) == (2030, "Sun", "Sun")
    assert local.utcoffset().total_seconds() == 10 * 3600 and not local.dst()


def test_the_summer_anchor_is_a_sunday_in_2030_in_daylight_saving():
    local = ANCHOR_SUMMER.astimezone(SYDNEY)
    assert (ANCHOR_SUMMER.year, ANCHOR_SUMMER.strftime("%a"), local.strftime("%a")) == (2030, "Sun", "Sun")
    assert local.utcoffset().total_seconds() == 11 * 3600 and local.dst()


def test_each_iso_string_is_its_anchor():
    assert datetime.fromisoformat(ANCHOR_ISO.replace("Z", "+00:00")) == ANCHOR
    assert datetime.fromisoformat(ANCHOR_SUMMER_ISO.replace("Z", "+00:00")) == ANCHOR_SUMMER


def test_conftest_re_exports_the_same_objects(request):
    """The conftest pytest LOADED, found as a plugin: importing it again would re-run its fences."""
    top = Path(__file__).with_name("conftest.py")
    [conftest] = [p for p in request.config.pluginmanager.get_plugins()
                  if getattr(p, "__file__", None) and Path(p.__file__) == top]
    assert (conftest.ANCHOR, conftest.ANCHOR_SUMMER) == (ANCHOR, ANCHOR_SUMMER)
    assert (conftest.ANCHOR_ISO, conftest.ANCHOR_SUMMER_ISO) == (ANCHOR_ISO, ANCHOR_SUMMER_ISO)
