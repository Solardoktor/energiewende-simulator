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
    assert len(at.get("plotly_chart")) == 3
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
    assert not at.error and len(at.get("plotly_chart")) == 3


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
    assert len(at.get("plotly_chart")) == 3


def _dl_traces(at):
    import json
    fig = json.loads(at.get("plotly_chart")[2].proto.spec)
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
    fig = json.loads(at.get("plotly_chart")[2].proto.spec)
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
    dl = json.loads(at.get("plotly_chart")[2].proto.spec)
    assert dl["layout"]["yaxis"]["title"]["text"].startswith("Superpower (−) bzw. Residuallast (+)")
    assert not at.metric                              # Kennzahlen jetzt als Ergebniszeilen
    md = " ".join(m.value for m in at.markdown)
    assert "EE-Anteil am Verbrauch" in md and "Heizgradtage (15 °C)" in md and "Referenz (Normaljahr" in md


def test_stufe2_elektrolyse_szenarien_und_grenzen():
    at = AppTest.from_file(APP, default_timeout=60).run()
    tab = at.dataframe[0].value
    assert "Elektrolyse" in tab["(Mehr-) Verbrauch"].tolist()
    i = tab["(Mehr-) Verbrauch"].tolist().index("Elektrolyse")
    assert tab["Erzeugung"].iloc[i] == "" and tab["Erzeugung"].tolist()[-2:] == ["Summe EE", "Residualerzeugung"]
    sector = next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung")
    sector.set_value("Szenario 2045").run()
    assert not at.exception, at.exception
    caps = " ".join(c.value for c in at.sidebar.caption)
    assert "Elektrolyse: 10 GW" in caps and "240 GWh" in caps
    assert any("Volllaststunden" in c.value for c in at.caption)
    assert "Elektrolyse 10 GW" in _dl_traces(at)[-1]
    next(c for c in at.sidebar.checkbox if "Sektorenkopplung" in c.label).check().run()
    ni = {n.label.split(" (")[0]: n for n in at.sidebar.number_input}
    assert ni["Elektro-PKW"].max == 50 and ni["Elektro-LKW"].max == 5.0
    assert ni["Elektrische Heizung"].max == 100 and ni["Elektrische Heizung"].label.endswith("[Mio. Einwohner]")
    assert ni["Elektrolyse"].value == 10 and ni["Elektrolyse"].max == 20 and ni["Elektrolyse"].label.endswith("[GW]")


def test_stufe3_speicherbilanz():
    at = AppTest.from_file(APP, default_timeout=60).run()
    before = at.dataframe[0].value.copy()
    next(r for r in at.sidebar.radio if r.label == "Szenario").set_value(
        "Agora Energiewende: Klimaneutrales Deutschland 2045").run()
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Szenario 2045").run()
    ref = at.dataframe[0].value.copy()
    next(s for s in at.sidebar.slider if s.label.startswith("Anteil Langzeit")).set_value(50).run()
    assert not at.exception, at.exception
    labels = [s.label for s in at.sidebar.slider]
    assert any(l.startswith("Speicherverlust") for l in labels) and any(l.startswith("Auslegungstemperatur") for l in labels)
    assert next(s for s in at.sidebar.slider if s.label.startswith("Auslegungstemperatur")).value == -7
    tab = at.dataframe[0].value                               # Stufe 4: mit Speicher, Referenz in Klammern
    i_sum = tab["(Mehr-) Verbrauch"].tolist().index("Summe Verbrauch")
    ref_sum = ref.loc[ref["(Mehr-) Verbrauch"] == "Summe Verbrauch", "Wert [TWh] "].item()
    assert tab["Wert [TWh] "].iloc[i_sum].endswith(f"({ref_sum})")
    assert "Speicher IN" in tab["(Mehr-) Verbrauch"].tolist()
    assert tab["Erzeugung"].tolist()[-2:] == ["Summe EE", "Residualerzeugung"]
    assert len(at.get("plotly_chart")) == 3
    assert any("Speicherbilanz" in h.value for h in at.subheader)
    kpi = at.dataframe[1].value
    assert "Ladestrom für Langzeit-Wärmespeicher aus Superpower" in kpi["Kennzahl"].tolist()
    assert "Mehrverbrauch Langzeitspeicher gesamt" in kpi["Kennzahl"].tolist()
    assert "Änderung Elektrolyse durch Mehrbedarf" not in kpi["Kennzahl"].tolist()
    ctrl = next(c.value for c in at.caption if c.value.startswith("Kontrolle"))
    assert "-0,000" in ctrl or " 0,000" in ctrl
    comp = next(d.value for d in at.dataframe if "Z0 Referenz (WP)" in d.value.columns)   # eingeklappt unten
    assert list(comp.columns) == ["Kennzahl", "Z0 Referenz (WP)", "Z1 direktelektrisch", "Z2 mit Speicher"]
    assert any(e.label.startswith("Vergleich der Rechenzustände") for e in at.expander)
    # WW-Prüfung: Warmwasser zu klein
    next(c for c in at.sidebar.checkbox if "Sektorenkopplung" in c.label).check().run()
    next(n for n in at.sidebar.number_input if n.label.startswith("Elektrische Warmwasserbereitung")).set_value(20).run()
    assert any("Warmwasser-Eingabe zu klein" in w.value for w in at.sidebar.warning)
    assert any("mindestens" in i.value for i in at.info)
    assert before is not None


def test_stufe4_darstellung_mit_ohne_speicher():
    import json
    at = AppTest.from_file(APP, default_timeout=60).run()
    next(r for r in at.sidebar.radio if r.label == "Szenario").set_value(
        "Agora Energiewende: Klimaneutrales Deutschland 2045").run()
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Szenario 2045").run()
    next(s for s in at.sidebar.slider if s.label.startswith("Anteil Langzeit")).set_value(50).run()
    assert not at.exception, at.exception
    charts_ = [json.loads(c.proto.spec) for c in at.get("plotly_chart")]
    main_names = [t.get("name") for t in charts_[0]["data"]]
    assert {"Speicher IN", "Speicher OUT (WW)", "Speicher OUT (Heiz)"} <= set(main_names)
    temp_names = [t.get("name") for t in charts_[1]["data"]]
    assert {"Tagesmitteltemperatur", "Speicher IN", "Speicherinhalt"} <= set(temp_names)
    assert "Aktuell ohne Speicher (Referenz)" in [t.get("name") for t in charts_[2]["data"]]
    # mit/ohne bei längster Dunkelflaute, Auswahl Top-10
    md = " ".join(m.value for m in at.markdown)
    assert "mit Speicher:" in md and "ohne Speicher:" in md
    top_radio = next(r for r in at.radio if r.label == "Top-10 Dunkelflauten")
    top10 = lambda: next(d.value for d in at.dataframe if "Gesamtdefizit [TWh]" in d.value.columns)  # noqa: E731
    top_mit = top10().copy()
    top_radio.set_value("ohne Speicher").run()
    assert not top10().equals(top_mit)
    # stationäres Jahr (Standard) und linear
    kpi = at.dataframe[1].value.set_index("Kennzahl")["Wert"]
    assert kpi["Rechenweise"] == "stationäres Jahr (zirkulär)" and "Speicherinhalt am Jahreswechsel" in kpi.index
    ctrl = next(c.value for c in at.caption if c.value.startswith("Kontrolle"))
    assert "0,000" in ctrl
    next(c for c in at.sidebar.checkbox if c.label.startswith("Speicher als stationäres Jahr")).uncheck().run()
    kpi = at.dataframe[1].value.set_index("Kennzahl")["Wert"]
    assert kpi["Rechenweise"] == "linear" and "Speicherinhalt aus Vorjahr" in kpi.index



def test_verbesserungen_hover_gw_achsen_hinweise():
    import json
    at = AppTest.from_file(APP, default_timeout=60).run()
    # Speicheranteil ohne zusätzliche Heizungs-EW → Hinweis statt stiller Wirkungslosigkeit
    next(s for s in at.sidebar.slider if s.label.startswith("Anteil Langzeit")).set_value(30).run()
    assert any("ohne Wirkung" in w.value for w in at.sidebar.warning)
    next(r for r in at.sidebar.radio if r.label == "Szenario").set_value(
        "Agora Energiewende: Klimaneutrales Deutschland 2045").run()
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Szenario 2045").run()
    assert not at.exception, at.exception
    main = json.loads(at.get("plotly_chart")[0].proto.spec)
    seg = next(t for t in main["data"] if t.get("name") == "Basis-Last")
    assert "GWh / %{customdata[1]:.1f} GW" in seg["hovertemplate"]
    lay = main["layout"]
    assert lay["yaxis2"]["overlaying"] == "y"
    assert lay["yaxis2"]["range"][1] * 24 == __import__("pytest").approx(lay["yaxis"]["range"][1])
    sub = lay["title"]["text"]
    assert "e-PKW (30 Mio.)" in sub and "e-LKW (3,0 Mio.)" in sub and "Elektrolyse (10 GW)" in sub
    out_h = next(t for t in main["data"] if t.get("name", "").startswith("Speicher OUT (Heiz)"))
    assert out_h["marker"]["pattern"]["bgcolor"] == "rgba(0,0,0,0)" and out_h["marker"]["color"] == "#d32f2f"
    assert out_h["name"] == "Speicher OUT (Heiz)"
    temp = json.loads(at.get("plotly_chart")[1].proto.spec)
    shapes = temp["layout"].get("shapes", [])
    assert len(shapes) == 2                                   # Heizgrenze und Auslegungstemperatur
    y2 = temp["layout"]["yaxis3"] if "yaxis3" in temp["layout"] else None
    if y2:                                                    # Gitter deckungsgleich: 4 Intervalle je Achse
        y_l = temp["layout"]["yaxis2"]
        assert (y_l["range"][1] - y_l["range"][0]) / y_l["dtick"] == 4
        assert (y2["range"][1] - y2["range"][0]) / y2["dtick"] == 4
    tabdf = at.dataframe[0].value
    tab = tabdf["(Mehr-) Verbrauch"].tolist()
    assert "Warmwasser (Netzbezug + Speicherentladung)" in tab
    assert not any(r.startswith(("Speicherentladung", "davon Mehrbedarf")) for r in tab)
    ww = tabdf.loc[tabdf["(Mehr-) Verbrauch"] == "Warmwasser (Netzbezug + Speicherentladung)", "Wert [TWh] "].item()
    assert " + " in ww and ww.endswith(")")
    # Hauptgrafik: rechte Achse in 5-GW-Schritten, linke Achse = × 24 h
    assert lay["yaxis2"]["dtick"] % 5 == 0 and lay["yaxis"]["dtick"] == lay["yaxis2"]["dtick"] * 24
    top10 = next(d.value for d in at.dataframe if "Gesamtdefizit [TWh]" in d.value.columns)
    assert "Jahresnaht" not in top10.columns
