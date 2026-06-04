from job_search_agent.dev_utils import parse_iso_datetime
from datetime import datetime, timezone


def test_parse_iso_datetime_zulu():
    dt = parse_iso_datetime("2023-01-01T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2023 and dt.hour == 12


def test_parse_iso_datetime_offset():
    dt = parse_iso_datetime("2023-01-01T12:00:00+02:00")
    assert dt is not None
    # converted to UTC should be 10:00
    assert dt.hour == 10
