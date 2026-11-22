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

    next(c for c in at.sidebar.checkbox if c.label == "Monatliche Skalierung aus").check().run()
    assert not at.exception
    assert not at.dataframe[0].value.equals(first)

    next(c for c in at.sidebar.checkbox if c.label == "Pumpspeicher/-Ladung aus").uncheck().run()
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
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Manuelle Werte").run()
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
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Manuelle Werte").run()
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
    assert kpi["Kennzahl"].tolist() == [
        "Einwohner, die mit Langzeit-Wärmespeichern versorgt werden", "Speicherentladung für Warmwasser + Heizung",
        "Ladeenergie für Langzeit-Wärmespeicher aus Superpower",
        "Mehrbedarf direktelektrische Speicherheizung für Warmwasser + Heizung", "Speicherverluste",
        "Mehrverbrauch Langzeitspeicher gesamt", "Max. Speicherinhalt", "Speicherdauer und Zyklenzahl",
        "Speicherkapazität", "Bemessungsleistung", "Ladeleistung", "Entladeleistung", "Nicht deckbarer Wärmebedarf",
        "Rechenweise", "Speicherinhalt am Jahreswechsel"]
    assert "Mehrverbrauch Langzeitspeicher gesamt" in kpi["Kennzahl"].tolist()
    assert "Änderung Elektrolyse durch Mehrbedarf" not in kpi["Kennzahl"].tolist()
    ctrl = next(c.value for c in at.caption if c.value.startswith("Kontrolle"))
    assert "-0,000" in ctrl or " 0,000" in ctrl
    comp = next(d.value for d in at.dataframe if "Z0 Referenz (WP)" in d.value.columns)   # eingeklappt unten
    assert list(comp.columns) == ["Kennzahl", "Z0 Referenz (WP)", "Z1 direktelektrisch", "Z2 mit Speicher"]
    assert any(e.label.startswith("Vergleich der Rechenzustände") for e in at.expander)
    # WW-Prüfung: Warmwasser zu klein
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Manuelle Werte").run()
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


def test_grafiken_ausgerichtet_und_seitenleiste_geordnet():
    import json
    at = AppTest.from_file(APP, default_timeout=60).run()
    next(r for r in at.sidebar.radio if r.label == "Szenario").set_value(
        "Agora Energiewende: Klimaneutrales Deutschland 2045").run()
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Szenario 2045").run()
    next(s for s in at.sidebar.slider if s.label.startswith("Anteil Langzeit")).set_value(50).run()
    assert not at.exception, at.exception
    main, temp = (json.loads(c.proto.spec)["layout"] for c in at.get("plotly_chart")[:2])
    assert main["margin"]["l"] == temp["margin"]["l"] and main["margin"]["r"] == temp["margin"]["r"]
    for ax in ("xaxis", "xaxis2"):
        assert temp[ax]["domain"] == [0, 1]                     # keine Reserve für die zweite Achse
    assert temp["yaxis3"]["side"] == "right" and temp["yaxis3"]["automargin"] is False
    assert main["yaxis"]["automargin"] is False
    # Seitenleiste: WP- und Speicherparameter eingeklappt, Speicheranteil sichtbar außerhalb
    exp = next(e for e in at.sidebar.expander if e.label.startswith("Wärmepumpen-Anteile"))
    inner = [s.label for s in exp.slider]
    assert inner[:2] == ["Anteil Luft-Wasser-WP (% des Rests)", "Anteil Wasser-Wasser-/Sole-WP (% des Rests)"]
    assert "Bemessungs-Speicherdauer (Tage)" in inner and not any(l.startswith("Anteil Langzeit") for l in inner)
    assert [c.label for c in exp.checkbox] == ["Speicher als stationäres Jahr rechnen"]
    labels = [s.label for s in at.sidebar.slider]
    weekend = "Anteil Ladeenergie gezielt am Wochenende/Feiertag (0 … 50 %)"
    assert weekend not in labels                              # nur mit „Manuelle Werte Sektorenkopplung“
    caps = [c.value for c in at.sidebar.caption]
    i_wp = next(i for i, c in enumerate(caps) if c.startswith("Luft-WP"))
    assert caps[i_wp + 1].startswith("Speicher-Einwohner")
    assert caps[i_wp + 2].startswith("Max. Tagesbedarf Elektrolyse")
    i_ely = next(i for i, c in enumerate(caps) if c.startswith("Max. Tagesbedarf Elektrolyse"))
    i_sum = next(i for i, c in enumerate(caps) if c.startswith("E-PKW:"))
    assert i_sum == i_ely + 1



def test_standardzeitraum_und_wochenende_eingeklappt():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert at.sidebar.radio[0].value == "Kalenderjahr"
    assert at.sidebar.selectbox[0].value == 2022
    assert any("01.01.2022 – 31.12.2022" in c.value for c in at.sidebar.caption)
    labels = lambda: [s.label for s in at.sidebar.slider]          # noqa: E731
    weekend = "Anteil Ladeenergie gezielt am Wochenende/Feiertag (0 … 50 %)"
    assert weekend not in labels()
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Manuelle Werte").run()
    assert weekend in labels()
    ni = [n.label for n in at.sidebar.number_input]
    i_lkw = next(i for i, l in enumerate(ni) if l.startswith("Elektro-LKW"))
    assert ni[i_lkw + 1].startswith("Elektrische Warmwasserbereitung")


def test_kostenvergleich_kasten():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not any(h.value.startswith("Kostenvergleich") for h in at.subheader)
    next(r for r in at.sidebar.radio if r.label == "Szenario").set_value(
        "Agora Energiewende: Klimaneutrales Deutschland 2045").run()
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Szenario 2045").run()
    next(s for s in at.sidebar.slider if s.label.startswith("Anteil Langzeit")).set_value(50).run()
    assert not at.exception, at.exception
    assert any(h.value == "Kostenvergleich Direktheizung vs. Speicherheizung" for h in at.subheader)
    tab = next(d.value for d in at.dataframe if "Kostenposition" in d.value.columns)
    names = tab["Kostenposition"].tolist()
    assert "Jahreskosten gesamt" in names and not any(n.startswith("zusätzliche Residualleistung") for n in names)
    i = names.index("davon zum Preis von Überschussstrom")
    assert names[i + 1] == "davon zum Preis von Erdgasstrom"
    j = names.index("Investition Langzeitspeicher")
    assert names[j + 1] == "Investition Residualkraftwerke" and names[j + 2] == "Investition gesamt"
    kpi = at.dataframe[1].value.set_index("Kennzahl")["Wert"]
    assert kpi["Speicherdauer und Zyklenzahl"].endswith("Zyklen (inter dies)")
    exp = next(e for e in at.sidebar.expander if e.label.startswith("Kostenparameter"))
    vals = {n.label: n.value for n in exp.number_input}
    assert vals["Wärmepumpe [€/kW(el)]"] == 8000 and vals["Langzeitspeicher HTS [€/kWh]"] == 15
    assert vals["Strompreis Erdgasstrom [ct/kWh]"] == 18 and vals["Strompreis Superpower [ct/kWh]"] == 1
    assert vals["Netzentgelte/Umlagen Direktheizung, unflexibel [ct/kWh]"] == 17
    assert vals["Netzentgelte/Umlagen Speicherheizung, flexibel [ct/kWh]"] == 10
    assert vals["Nutzungsdauer Wärmepumpen [Jahre]"] == 20 and vals["Nutzungsdauer Erdgaskraftwerke [Jahre]"] == 40
    assert vals["Nutzungsdauer Langzeit-Wärmespeicher [Jahre]"] == 50
    before = tab.copy()
    next(n for n in exp.number_input if n.label.startswith("Langzeitspeicher HTS")).set_value(5).run()
    after = next(d.value for d in at.dataframe if "Kostenposition" in d.value.columns)
    assert not after.equals(before)


def test_status_quo_heute_ist_standard():
    at = AppTest.from_file(APP, default_timeout=60).run()
    radio = next(r for r in at.sidebar.radio if r.label == "Szenario")
    assert radio.value == "Status Quo (Ist-Leistung heute)" and radio.options[0] == radio.value
    assert "Status Quo (Ist-Leistung am Zeitraumende)" in radio.options
    caps = " ".join(c.value for c in at.sidebar.caption)
    inst = synthetic_installed().iloc[-1]                     # neuester Monat der Testdaten
    assert f"Stand der installierten Leistung: {inst['month_end']:%m/%Y}" in caps
    assert f"Solar: {inst['solar']:.1f}".replace(".", ",") in caps
    radio.set_value("Status Quo (Ist-Leistung am Zeitraumende)").run()
    caps2 = " ".join(c.value for c in at.sidebar.caption)
    assert "Stand der installierten Leistung" not in caps2 and caps2 != caps


def test_manuelle_werte_als_szenario_und_einklappbereiche():
    at = AppTest.from_file(APP, default_timeout=60).run()
    build = next(r for r in at.sidebar.radio if r.label == "Szenario")
    sector = next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung")
    assert build.options[-1] == "Manuelle Werte" and sector.options[-1] == "Manuelle Werte"
    assert not any(c.label.startswith("Manuelle") for c in at.sidebar.checkbox)      # keine Haken mehr
    exp_b = next(e for e in at.sidebar.expander if e.label == "Manuelle Werte und Optionen")
    assert [c.label for c in exp_b.checkbox] == ["Monatliche Skalierung aus", "Pumpspeicher/-Ladung aus"]
    exp_s = next(e for e in at.sidebar.expander if e.label == "Last-Skalierung und manuelle Werte")
    assert [s.label for s in exp_s.slider] == ["Last-Skalierung (%)"]
    # Ausbau: Agora wählen, dann manuell → Vorbelegung 400 GWp
    build.set_value("Agora Energiewende: Klimaneutrales Deutschland 2045").run()
    next(r for r in at.sidebar.radio if r.label == "Szenario").set_value("Manuelle Werte").run()
    exp_b = next(e for e in at.sidebar.expander if e.label == "Manuelle Werte und Optionen")
    assert next(n for n in exp_b.number_input if n.label == "Ziel Solar (GWp)").value == 400
    # Sektorenkopplung: manuelle Eingaben im Einklappbereich, nach der Last-Skalierung
    next(r for r in at.sidebar.radio if r.label == "Szenario Sektorenkopplung").set_value("Manuelle Werte").run()
    exp_s = next(e for e in at.sidebar.expander if e.label == "Last-Skalierung und manuelle Werte")
    assert [s.label for s in exp_s.slider][0] == "Last-Skalierung (%)"
    assert {n.label.split(" (")[0] for n in exp_s.number_input} == {
        "Elektro-PKW", "Elektro-LKW", "Elektrische Warmwasserbereitung", "Elektrische Heizung", "Elektrolyse"}
    # Reihenfolge der Abschnitte: Temperatur-Standort direkt vor CO₂
    labels = [w.label for w in at.sidebar.get("selectbox")] + [n.label for n in at.sidebar.number_input]
    assert "Standort Temperaturdaten" in labels
    md = [m.value for m in at.sidebar.markdown]
    assert any("Temperatur (Heizung, Mobilität)" in m for m in md)
    assert not at.exception
