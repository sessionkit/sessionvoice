"""The test-time anchor (EARS-397): the ONE synthetic moment a test reaches for when it
needs a date or time that nothing else compares with.

Defined here once; every test imports it (``from tests._anchor import ANCHOR``) and never
redefines it. A lone typed date becomes ANCHOR or a named variant of it
(``ANCHOR - timedelta(days=1)``); use ANCHOR_SUMMER where the value must be in daylight
saving. A test that needs "today" is written relative to the real now instead, and the
code keeps reading the real clock: this is a value, not a fake clock.
"""
from datetime import datetime, timezone

ANCHOR = datetime(2030, 6, 9, 12, 34, 56, tzinfo=timezone.utc)          # Sunday, +10:00 Sydney, no DST
ANCHOR_SUMMER = datetime(2030, 12, 8, 12, 34, 56, tzinfo=timezone.utc)  # Sunday, +11:00, DST
ANCHOR_ISO = "2030-06-09T12:34:56Z"
ANCHOR_SUMMER_ISO = "2030-12-08T12:34:56Z"
