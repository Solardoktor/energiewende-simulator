from datetime import date, datetime, timedelta, timezone

import pandas as pd

from energiewende import config
from energiewende.store import Entry, Store, max_age_days, needs_refresh

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def entry(age_days, complete_until):
    return Entry(pd.DataFrame(), NOW - timedelta(days=age_days), complete_until, "data/")


def test_max_age_regeln():
    today = date(2026, 10, 26)
    assert max_age_days(2023, today) == config.MAX_AGE_OLD_YEARS_DAYS     # jährlich
    assert max_age_days(2025, today) == config.MAX_AGE_RECENT_DAYS        # letztes vollständiges Jahr: monatlich
    assert max_age_days(2026, today) == config.MAX_AGE_RECENT_DAYS


def test_needs_refresh():
    end = date(2025, 12, 31)
    assert needs_refresh(None, end, 30)
    assert not needs_refresh(entry(10, end), end, 30, NOW)
    assert needs_refresh(entry(31, end), end, 30, NOW)
    assert not needs_refresh(entry(0.1, date(2025, 12, 1)), end, 30, NOW)   # Mindestabstand für Retry
    assert needs_refresh(entry(0.5, date(2025, 12, 1)), end, 30, NOW)


def test_store_waehlt_juengsten_abruf(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    Store([a], a).save("x", pd.DataFrame({"v": [1]}), date(2024, 1, 1))
    Store([b], b).save("x", pd.DataFrame({"v": [2]}), date(2024, 1, 1))
    e = Store([a, b], b).load("x")
    assert e.df["v"].iloc[0] == 2
