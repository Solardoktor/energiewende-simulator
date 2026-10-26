from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from conftest import v2_power_json
from energiewende import data
from energiewende.store import Store


def daily_of(js):
    return data.power_to_daily_energy(js).set_index("date")


def test_tagesgrenzen_in_ortszeit_keine_extrazeile():
    d = daily_of(v2_power_json("2024-01-01", "2024-01-03"))
    assert list(d.index) == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")]
    assert d["load_incl_self_consumption"].tolist() == pytest.approx([24.0, 24.0])


@pytest.mark.parametrize("start,end,expected", [
    ("2024-03-30", "2024-04-02", [24, 23, 24]),   # Sommerzeit-Beginn
    ("2024-10-26", "2024-10-29", [24, 25, 24]),   # Sommerzeit-Ende
])
def test_zeitumstellung(start, end, expected):
    d = daily_of(v2_power_json(start, end))
    assert d["load_incl_self_consumption"].tolist() == pytest.approx(expected)
    assert d["coverage"].tolist() == pytest.approx([1, 1, 1])


def test_stundenwerte_und_naive_zeitstempel():
    d = daily_of(v2_power_json("2023-06-01", "2023-06-03", freq="60min", naive=True))
    assert d["load_incl_self_consumption"].tolist() == pytest.approx([24.0, 24.0])


def test_wechsel_der_aufloesung():
    a = v2_power_json("2025-09-30", "2025-10-01", freq="60min")
    b = v2_power_json("2025-10-01", "2025-10-02", freq="15min")
    d = daily_of({"unit": "MW", "data": a["data"] + b["data"]})
    assert d["load_incl_self_consumption"].tolist() == pytest.approx([24.0, 24.0])


def test_luecke_senkt_coverage():
    d = daily_of(v2_power_json("2024-05-01", "2024-05-02", drop=slice(10, 22)))   # 3 h fehlen
    assert d["coverage"].iloc[0] == pytest.approx(21 / 24)
    assert d["load_incl_self_consumption"].iloc[0] == pytest.approx(21.0)


def test_einheit_gw():
    js = v2_power_json("2024-05-01", "2024-05-02", mw={"solar": 2.0})
    js["unit"] = "GW"
    assert daily_of(js)["solar"].iloc[0] == pytest.approx(48.0)


def test_installed_monat_und_interpolation():
    js = {"unit": "GW", "data": [
        {"timestamp": "2024-02-01T00:00:00+01:00", "values": {"solar_dc": 90.0, "wind_onshore": 60, "wind_offshore": 8}},
        {"timestamp": "2024-03-01T00:00:00+01:00", "values": {"solar_dc": 92.9, "wind_onshore": 61, "wind_offshore": 8}},
    ]}
    inst = data.parse_installed(js)
    assert inst["month_end"].tolist() == [pd.Timestamp("2024-02-29"), pd.Timestamp("2024-03-31")]
    daily, fb = data.daily_installed(inst, pd.date_range("2024-02-29", "2024-03-31"))
    assert not fb
    assert daily.loc["2024-02-29", "solar"] == pytest.approx(90.0)
    assert daily.loc["2024-03-31", "solar"] == pytest.approx(92.9)
    assert daily.loc["2024-03-15", "solar"] == pytest.approx(90.0 + 2.9 * 15 / 31)


def test_installed_fallback():
    daily, fb = data.daily_installed(pd.DataFrame(), pd.date_range("2024-01-01", "2024-01-03"))
    assert fb and (daily["solar"] > 0).all()


def _fake_api(calls, year_data_until=None, fail=False):
    def fetch(url, params, timeout=None):
        calls.append(params)
        if fail:
            raise ConnectionError("offline")
        end = date.fromisoformat(params["end"])
        if year_data_until:
            end = min(end, year_data_until + timedelta(days=1))
        return v2_power_json(params["start"], end.isoformat())
    return fetch


def test_load_period_puffer_zuschnitt_und_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(data, "fetch_json", _fake_api(calls))
    store = Store([tmp_path], tmp_path)
    r = data.load_power_period(date(2023, 1, 1), date(2023, 12, 31), date(2026, 10, 26), store)
    assert len(r.df) == 365 and r.df["coverage"].min() == pytest.approx(1)
    assert calls[0]["start"] == "2022-12-31" and calls[0]["end"] == "2024-01-01"
    data.load_power_period(date(2023, 1, 1), date(2023, 12, 31), date(2026, 10, 26), store)
    assert len(calls) == 1      # zweiter Aufruf aus der Ablage


def test_letzte_365_tage_aus_zwei_jahren(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(data, "fetch_json", _fake_api(calls))
    r = data.load_power_period(date(2025, 10, 1), date(2026, 9, 30), date(2026, 10, 26), Store([tmp_path], tmp_path))
    assert len(r.df) == 365 and len(calls) == 2
    assert r.df["date"].iloc[0] == pd.Timestamp("2025-10-01")


def test_veraltete_daten_werden_bei_fehler_weiterverwendet(tmp_path, monkeypatch):
    store = Store([tmp_path], tmp_path)
    monkeypatch.setattr(data, "fetch_json", _fake_api([]))
    data.load_power_period(date(2025, 1, 1), date(2025, 12, 31), date(2026, 10, 26), store)
    # Metadaten künstlich altern lassen → Aktualisierung fällig, API fällt aus
    meta = tmp_path / "power_2025.json"
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    meta.write_text(meta.read_text().replace(meta.read_text().split('"fetched_at": "')[1].split('"')[0], old))
    monkeypatch.setattr(data, "fetch_json", _fake_api([], fail=True))
    r = data.load_power_period(date(2025, 1, 1), date(2025, 12, 31), date(2026, 10, 26), store)
    assert len(r.df) == 365 and any("Aktualisierung fehlgeschlagen" in n for n in r.notes)


def test_unvollstaendiges_laufendes_jahr(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(data, "fetch_json", _fake_api(calls, year_data_until=date(2026, 9, 28)))
    r = data.load_power_period(date(2025, 10, 1), date(2026, 9, 30), date(2026, 10, 2), Store([tmp_path], tmp_path))
    assert (r.df["coverage"] < 1).sum() == 2 and any("unvollständig" in n for n in r.notes)


def test_rate_limit_bricht_sofort_ab(monkeypatch):
    import time
    import requests

    def fake_get(self, url, params=None, timeout=None):
        r = requests.Response()
        r.status_code, r._content, r.headers["Retry-After"] = 429, b"{}", "60"
        return r

    monkeypatch.setattr(requests.Session, "get", fake_get)
    t0 = time.perf_counter()
    with pytest.raises(data.RateLimited):
        data.fetch_json("https://example.invalid/x", {})
    assert time.perf_counter() - t0 < 2
