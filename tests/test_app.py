"""Smoke-Test der Streamlit-App mit synthetischen Daten (ohne Netzzugriff)."""
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from conftest import synthetic_installed, synthetic_raw, synthetic_temps
from energiewende import data

APP = str(Path(__file__).resolve().parent.parent / "SDR_TOOL_Energiewende-Simulator.py")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    st.cache_data.clear()

    def power(start, end, today, store=None):
        raw = synthetic_raw(start, end)
        return data.LoadResult(raw, [], ["synthetisch"])

    monkeypatch.setattr(data, "load_power_period", power)
    monkeypatch.setattr(data, "load_installed", lambda today, store=None: data.LoadResult(synthetic_installed()))
    monkeypatch.setattr(data, "load_temperatures",
                        lambda loc, s, e, today, store=None: data.LoadResult(synthetic_temps(s, e)))
    monkeypatch.setattr(data, "load_hgt_reference", lambda loc, store=None: (2300.0, []))


def test_app_laeuft_und_schalter_wirken():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 2
    first = at.dataframe[0].value.copy()

    at.sidebar.checkbox[1].check().run()          # Monatliche Skalierung aus
    assert not at.exception
    assert not at.dataframe[0].value.equals(first)

    at.sidebar.checkbox[2].uncheck().run()        # Pumpspeicher ein
    assert not at.exception

    at.sidebar.radio[0].set_value("Kalenderjahr").run()
    assert not at.exception
    at.sidebar.selectbox[0].set_value(2016).run()  # Jahr (vor dem Standort)
    assert not at.exception


def test_fehlschlag_wird_nicht_gecacht(monkeypatch):
    state = {"ok": False}

    def power(start, end, today, store=None):
        if not state["ok"]:
            return data.LoadResult(pd.DataFrame(), ["Rate-Limit"], [])
        return data.LoadResult(synthetic_raw(start, end), [], ["synthetisch"])

    monkeypatch.setattr(data, "load_power_period", power)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert any("Keine Energiedaten" in e.value for e in at.error)
    state["ok"] = True
    at.run()
    assert not at.error and len(at.get("plotly_chart")) == 2


def test_heizung_eingabe_ohne_netzabruf(monkeypatch, tmp_path):
    """Höhere Heizungs-Eingabe darf keinen (langsamen) Netzabruf auslösen."""
    monkeypatch.undo()
    st.cache_data.clear()
    monkeypatch.setattr(data, "load_power_period",
                        lambda s, e, t, store=None: data.LoadResult(synthetic_raw(s, e), [], ["x"]))
    monkeypatch.setattr(data, "load_installed", lambda t, store=None: data.LoadResult(synthetic_installed()))
    monkeypatch.setattr(data, "load_temperatures",
                        lambda l, s, e, t, store=None: data.LoadResult(synthetic_temps(s, e)))
    monkeypatch.setattr(data, "fetch_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Netz")))
    at = AppTest.from_file(APP, default_timeout=60).run()
    next(c for c in at.sidebar.checkbox if "Sektorenkopplung" in c.label).check().run()
    for label in ("Warmwasser", "Heizung"):
        ni = next(n for n in at.sidebar.number_input if label in n.label)
        ni.set_value(80).run()
        assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 2


def _dl_traces(at):
    import json
    fig = json.loads(at.get("plotly_chart")[1].proto.spec)
    return [t["name"] for t in fig["data"] if t.get("showlegend", True)]


def test_sektorszenarien_und_dauerlinien_vergleich():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    names = _dl_traces(at)
    assert len(names) == 1 and names[0].startswith("Aktuell:") and "PKW 2" in names[0]

    sector = next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung")
    sector.set_value("Szenario 2030").run()
    assert not at.exception, at.exception
    names = _dl_traces(at)
    assert len(names) == 2
    assert names[0].startswith("Vorher:") and "PKW 2" in names[0]
    assert names[1].startswith("Aktuell:") and "PKW 10" in names[1] and "Heiz. 20" in names[1]
    cap = [c.value for c in at.sidebar.caption if c.value.startswith("E-PKW")][0]
    assert "10 Mio" in cap and "25 Mio EW" in cap

    # Rerun ohne Parameteränderung (z. B. CO₂) ändert den Vergleich nicht
    at.sidebar.number_input[-1].set_value(800).run()
    assert _dl_traces(at) == names

    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Szenario 2045").run()
    names2 = _dl_traces(at)
    assert "PKW 10" in names2[0] and "PKW 30" in names2[1] and "LKW 3,0" in names2[1]

    at.button[0].click().run()          # Vergleich zurücksetzen
    assert len(_dl_traces(at)) == 1


def test_dauerlinie_stundenachse():
    import json
    at = AppTest.from_file(APP, default_timeout=60).run()
    fig = json.loads(at.get("plotly_chart")[1].proto.spec)
    n = fig["layout"]["xaxis"]["range"][1]
    assert fig["layout"]["xaxis2"]["range"] == [24, 24 * n]
    assert fig["layout"]["xaxis2"]["overlaying"] == "x" and fig["layout"]["xaxis2"]["side"] == "top"
    assert "Stunden [h]" in fig["layout"]["xaxis"]["title"]["text"]


def test_stufe1_hover_achse_kennzahlen():
    import json
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    main = json.loads(at.get("plotly_chart")[0].proto.spec)
    seg = next(t for t in main["data"] if t["name"] == "Basis-Last")
    assert "customdata" in seg and "%{customdata" in seg["hovertemplate"]
    dl = json.loads(at.get("plotly_chart")[1].proto.spec)
    assert dl["layout"]["yaxis"]["title"]["text"].startswith("Superpower (−) bzw. Residuallast (+)")
    assert not at.metric                              # Kennzahlen jetzt als Ergebniszeilen
    md = " ".join(m.value for m in at.markdown)
    assert "EE-Anteil am Verbrauch" in md and "Heizgradtage (15 °C)" in md and "Referenz (Normaljahr" in md
