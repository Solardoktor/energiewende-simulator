"""
Energiewende-Simulator (Solar Doktor)
Streamlit-App für eine skalierte Tagesenergie-Übersicht auf Basis von Energy-Charts-Daten.
Annahme: Intra-Day-Abweichungen durch Speicher und Flexibilitäten ausgleichbar.

Start:  streamlit run SDR_TOOL_Energiewende-Simulator.py
"""

import base64
import html
import time
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import holidays
import pandas as pd
import streamlit as st

from energiewende import charts, config, costs, data, model, storage, texts
from energiewende.model import german_number as gn
from energiewende.store import last_day_of_previous_month

st.set_page_config(page_title="Energiewende-Simulator", page_icon="⚡", layout="wide",
                   initial_sidebar_state="expanded")
APP_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Gecachte Lade- und Rechenfunktionen
# (``today`` ist Teil des Cache-Schlüssels → spätestens täglich neu bewertet)
# ---------------------------------------------------------------------------
class DataUnavailable(Exception):
    """Wird in gecachten Ladefunktionen geworfen, damit Fehlschläge NICHT gecacht werden."""


@st.cache_data(ttl=3600, show_spinner=False)
def load_power(start: date, end: date, today: date):
    r = data.load_power_period(start, end, today)
    if r.df.empty or r.df["coverage"].sum() == 0:
        raise DataUnavailable(r.notes)
    return r.df, r.notes, r.sources


@st.cache_data(ttl=3600, show_spinner=False)
def load_installed(today: date):
    r = data.load_installed(today)
    if r.df.empty:
        raise DataUnavailable(r.notes)
    return r.df, r.notes, r.sources


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_temps(location: str, start: date, end: date, today: date):
    r = data.load_temperatures(location, start, end, today)
    if r.df.empty:
        raise DataUnavailable(r.notes)
    return r.df, r.notes, r.sources


@st.cache_data(ttl=3600, show_spinner=False)
def load_hgt_reference(location: str):
    """Nur Lesen vorberechneter Werte – kein Netzzugriff (kann nicht hängen)."""
    return data.load_hgt_reference(location)


def timed(label, fn, *args):
    """Ruft fn auf, misst die Dauer und fängt DataUnavailable ab (→ None, Hinweise)."""
    t0 = time.perf_counter()
    try:
        out, notes = fn(*args), []
    except DataUnavailable as e:
        out, notes = None, list(e.args[0]) if e.args else []
    timings.append(f"{label}: {time.perf_counter() - t0:.1f} s")
    return out, notes


@st.cache_data(show_spinner=False, max_entries=64)
def run_model(raw, inst_daily, temps, holiday_dates, params: model.Params):
    df = model.compute_daily(raw, inst_daily, temps, holiday_dates, params)   # Zustand Z0 (Referenz)
    return df, model.summarize(df, params, cyclic=True), storage.compute_storage(df, params)


def fmt_period(p: model.Period | None) -> str:
    if p is None:
        return "0 Tage"
    wrap = " ↻" if p.wraps else ""
    return f"{p.days} Tage ({p.start:%d.%m.%Y} bis {p.end:%d.%m.%Y}{wrap})"


def winter_options(today: date) -> list[int]:
    """Startjahre der abgeschlossenen Winterjahre (1.7. – 30.6.), neuestes zuerst."""
    last_complete = last_day_of_previous_month(today)
    newest = last_complete.year - 1 if last_complete >= date(last_complete.year, 6, 30) else last_complete.year - 2
    return list(range(newest, config.FIRST_YEAR - 1, -1))


def result_line(label: str, value: str, note: str | None = None, target=st) -> None:
    """Ergebniszeile wie st.metric, aber mit kleinerer Schrift für lange Werte.

    ``target``: Ausgabeort (z. B. eine Spalte); ``note``: optionale kleine Zusatzzeile unter dem Wert.
    """
    note_html = (f"<div style='font-size:0.8rem;opacity:0.65'>{html.escape(note)}</div>" if note else "")
    target.markdown(
        f"<div style='margin:0.2rem 0 0.8rem 0'>"
        f"<div style='font-size:0.9rem;opacity:0.75'>{html.escape(label)}</div>"
        f"<div style='font-size:1.2rem;font-weight:600;line-height:1.4'>{html.escape(value)}</div>"
        f"{note_html}</div>",
        unsafe_allow_html=True)


def render_cost_comparison(c: dict | None, cp: costs.CostParams) -> None:
    """Kasten „Kostenvergleich Direktheizung vs. Speicherheizung“."""
    if c is None:
        return
    st.subheader("Kostenvergleich Direktheizung vs. Speicherheizung")
    st.markdown(texts.COST_INTRO)
    a, b = c["a"], c["b"]
    mrd = lambda v: gn(v, 1)                                  # noqa: E731
    rows = [
        ("Einwohner", "Mio. EW", gn(c["n_s"]), gn(c["n_s"]), None),
        ("Nutzwärme Heizung + Warmwasser", "TWh/a", gn(c["heat_twh"]), gn(c["heat_twh"]), None),
        ("Strombezug", "TWh/a", gn(a["load_twh"]), gn(b["load_twh"]), b["load_twh"] - a["load_twh"]),
        ("davon zum Preis von Überschussstrom", "TWh/a", gn(a["surplus_twh"]), gn(b["surplus_twh"]),
         b["surplus_twh"] - a["surplus_twh"]),
        ("davon zum Preis von Erdgasstrom", "TWh/a", gn(a["gas_twh"]), gn(b["gas_twh"]),
         b["gas_twh"] - a["gas_twh"]),
        ("mittlerer Strompreis (ohne Netzentgelte)", "ct/kWh", gn(a["price_ct"]), gn(b["price_ct"]),
         b["price_ct"] - a["price_ct"]),
        ("Wärmepumpenleistung", "GW(el)", gn(a["wp_gw"]), gn(b["wp_gw"]), b["wp_gw"] - a["wp_gw"]),
        ("Höchste Residualleistung (Jahresdauerlinie)", "GW", gn(c["peak_res_z0_gw"]), gn(c["peak_res_z2_gw"]),
         c["peak_res_z2_gw"] - c["peak_res_z0_gw"]),
        ("Speicherkapazität (HTS) = max. Speicherinhalt", "TWh", gn(a["cap_twh"], 2), gn(b["cap_twh"], 2),
         b["cap_twh"] - a["cap_twh"]),
        ("Investition Wärmepumpen", "Mrd. €", mrd(a["inv_wp"]), mrd(b["inv_wp"]), b["inv_wp"] - a["inv_wp"]),
        ("Investition Langzeitspeicher", "Mrd. €", mrd(a["inv_sto"]), mrd(b["inv_sto"]), b["inv_sto"] - a["inv_sto"]),
        ("Investition Residualkraftwerke", "Mrd. €", mrd(a["inv_res"]), mrd(b["inv_res"]), b["inv_res"] - a["inv_res"]),
        ("Investition gesamt", "Mrd. €",
         mrd(a["invest"]), mrd(b["invest"]), b["invest"] - a["invest"]),
        (f"Annuität Investitionen (WP {cp.life_wp_years} J., KW {cp.life_res_years} J., Speicher "
         f"{cp.life_sto_years} J.; {gn(cp.interest_pct)} %)", "Mrd. €/a",
         mrd(a["annuity"]), mrd(b["annuity"]), b["annuity"] - a["annuity"]),
        ("Energiekosten", "Mrd. €/a", mrd(a["energy"]), mrd(b["energy"]), b["energy"] - a["energy"]),
        ("Netzentgelte/Umlagen", "Mrd. €/a", mrd(a["fees"]), mrd(b["fees"]), b["fees"] - a["fees"]),
        ("Jahreskosten gesamt", "Mrd. €/a", mrd(a["total"]), mrd(b["total"]), b["total"] - a["total"]),
        ("Jahreskosten je Einwohner", "€/a", gn(a["per_ew"], 0), gn(b["per_ew"], 0), b["per_ew"] - a["per_ew"]),
        ("Jahreskosten je kWh Nutzwärme", "ct/kWh", gn(a["ct_per_kwh_heat"]), gn(b["ct_per_kwh_heat"]),
         b["ct_per_kwh_heat"] - a["ct_per_kwh_heat"]),
    ]
    def decimals(unit: str) -> int:
        return {"€/a": 0, "TWh": 2}.get(unit, 1)

    table = pd.DataFrame([{
        "Kostenposition": n, "Einheit": u, "Direktheizung (WP-Mix)": va, "Speicherheizung (HTS)": vb,
        "Differenz Speicher − Direkt": "" if d is None else (("+" if d > 0 else "") + gn(d, decimals(u))),
    } for n, u, va, vb, d in rows])
    st.dataframe(table, width="stretch", hide_index=True)
    diff = b["total"] - a["total"]
    st.caption(("Speicherheizung günstiger um " if diff < 0 else "Speicherheizung teurer um ")
               + f"{gn(abs(diff), 1)} Mrd. €/a ({gn(abs(diff) * 1e9 / (c['n_s'] * 1e6), 0)} € je Einwohner und Jahr). "
               + texts.COST_NOTES.strip().replace("\n", " "))


def render_state_comparison(r: storage.StorageResult, s0: dict) -> None:
    """Vergleich der Rechenzustände Z0 / Z1 / Z2 (eingeklappt unter den Erläuterungen)."""
    t = lambda gwh: gn(gwh / 1000.0, 2)                       # noqa: E731

    def longest(sx, key):
        return sx[key][0].days if sx[key] else 0
    comp = pd.DataFrame({
        "Kennzahl": ["Summe Verbrauch [TWh]", "Residualerzeugung [TWh]", "Superpower [TWh]",
                     "EE-Anteil am Verbrauch", "Längste Dunkelflaute [Tage]", "Tage mit EE-Defizit",
                     "Längste Superpower [Tage]"],
        **{name: [t(sx["sums_gwh"]["total_load_gwh"]), t(sx["sums_gwh"]["residual_day"]),
                  t(sx["sums_gwh"]["superpower_day"]), gn(sx["ee_share"] * 100) + " %",
                  str(longest(sx, "deficit_periods")), str(sx["deficit_days"]), str(longest(sx, "surplus_periods"))]
           for name, sx in (("Z0 Referenz (WP)", s0), ("Z1 direktelektrisch", r.summary_z1),
                            ("Z2 mit Speicher", r.summary_z2))},
    })
    st.markdown("Z0: ohne Speicher (Wärmepumpen-Mix), Z1: Speicher-Haushalte direktelektrisch ohne Verschiebung, "
                "Z2: mit Speicher (Hauptergebnis)")
    st.dataframe(comp, width="stretch", hide_index=True)


def describe_run(p: model.Params, start: date, end: date, location: str,
                 ev, lkw, dhw, heat) -> str:
    """Kurzbeschreibung der Parameter eines Berechnungslaufs (für die Legende)."""
    gen = (f"Solar {p.target_solar:.0f} GWp, Wind {p.target_wind_onshore:.0f}+{p.target_wind_offshore:.0f} GW"
           if p.scaling_enabled else "Ist-Leistung (unskaliert)")
    extras = []
    if p.include_pumped_storage:
        extras.append("inkl. Pumpspeicher")
    if p.load_scale != 1.0:
        extras.append(f"Last {p.load_scale * 100:.0f} %")
    if location != config.DEFAULT_LOCATION:
        extras.append(f"Temp. {location}")
    if p.electrolysis_gw > 0:
        extras.append(f"Elektrolyse {gn(p.electrolysis_gw, 0)} GW")
    if p.storage_share > 0:
        extras.append(f"LZ-Wärmespeicher {p.storage_share * 100:.0f} %, Bemessung {p.storage_days} Tage, "
                      f"Verlust {gn(p.storage_loss * 100)} %/Tag" + ("" if p.storage_cyclic else ", linear")
                      + (", Speicher vor Elektrolyse" if p.storage_first and p.electrolysis_gw > 0 else ""))
    return (f"{start:%d.%m.%Y}–{end:%d.%m.%Y} | {gen}<br>"
            f"PKW {gn(ev, 0)} | LKW {gn(lkw)} | WW {gn(dhw, 0)} | Heiz. {gn(heat, 0)} Mio | "
            f"WP Luft/Sole {p.share_air_hp * 100:.0f}/{p.share_water_hp * 100:.0f} % | "
            f"WE-Laden {p.weekend_share * 100:.0f} %" + (" | " + ", ".join(extras) if extras else ""))


def render_storage_balance(r: storage.StorageResult, s0: dict) -> None:
    """Abschnitt „Speicherbilanz“ (Stufe 3: Zahlen; Grafiken folgen in Stufe 4)."""
    st.subheader("Speicherbilanz Langzeit-Wärmespeicher")
    for n in r.notes:
        st.info(n)
    if not r.active:
        return
    k = r.kpi
    t = lambda gwh: gn(gwh / 1000.0, 2)                       # noqa: E731  GWh → TWh
    pct = lambda a, b: gn(100.0 * a / b, 1) + " %" if b else "–"   # noqa: E731
    out_sum = k["out_dhw"] + k["out_heat"]
    extra = k["extra_dhw"] + k["extra_heat"]
    more = extra + k["loss"] - (0.0 if k["cyclic"] else k["prev_year"])
    design_gwh_day = k["p_design_gw"] * 24.0
    rows = [
        ("Einwohner, die mit Langzeit-Wärmespeichern versorgt werden", f"{gn(k['n_s'])} Mio. EW",
         "Einwohner, deren neue Elektro-Heizungen als Langzeit-Wärmespeicher gebaut werden"),
        ("Speicherentladung für Warmwasser + Heizung",
         f"{t(k['out_dhw'])} + {t(k['out_heat'])} TWh (Σ {t(k['out_dhw'] + k['out_heat'])} TWh)",
         "ausgespeicherte Wärme (COP = 1)"),
        ("Ladeenergie für Langzeit-Wärmespeicher aus Superpower", f"{t(k['in'])} TWh",
         "eingespeicherter Stromüberschuss; " + ("Speicherbeladung hat Vorrang vor Elektrolyse"
                                                if k["storage_first"] else "Elektrolyse hat Vorrang")),
        ("Mehrbedarf direktelektrische Speicherheizung für Warmwasser + Heizung",
         f"{t(k['extra_dhw'])} + {t(k['extra_heat'])} TWh (Σ {t(extra)} TWh; "
         f"+{pct(k['extra_dhw'], k['dhw_z0'])} bzw. +{pct(k['extra_heat'], k['heat_z0'])})",
         "COP 1 statt Wärmepumpen-Mix mit Tages-COP (Heizung) bzw. +50 % (Warmwasser); % bezogen auf ohne Speicher"),
        ("Speicherverluste", f"{t(k['loss'])} TWh ({pct(k['loss'], k['in'] + k['prev_year'])})",
         "Summe der Tagesverluste; % bezogen auf eingespeicherte Energie (IN + Inhalt aus Vorjahr)"),
        ("Mehrverbrauch Langzeitspeicher gesamt", f"{t(more)} TWh ({pct(more, k['total_z0'])})",
         "Stromverbrauch mit − ohne Speicher = Mehrbedarf Direktelektrisch + Speicherverluste"
         + ("" if k["cyclic"] else " − Inhalt aus Vorjahr") + " (ohne Rückwirkung auf die Elektrolyse)"),
        ("Max. Speicherinhalt",
         f"{t(k['soc_max'])} TWh ({gn(k['soc_max'] / k['n_s'], 0) if k['n_s'] else '–'} kWh je Speicher-EW, "
         f"{k['soc_max_date']:%d.%m.%Y})", "Maximum des Speicherinhalts am Tagesende"),
        ("Speicherdauer und Zyklenzahl",
         f"{gn(k['soc_max'] / design_gwh_day, 1) if design_gwh_day > 0 else '–'} Tage / "
         f"{gn((k['out_dhw'] + k['out_heat']) / k['soc_max'], 1) if k['soc_max'] > 0 else '–'} Zyklen (inter dies)",
         "Max. Speicherinhalt ÷ Bemessungsleistung; Summe Speicherentladung ÷ max. Speicherinhalt"),
        ("Speicherkapazität", f"{t(k['capacity'])} TWh",
         f"Bemessungsleistung × Bemessungs-Speicherdauer ({k['days_param']} Tage); Obergrenze des Speicherinhalts"),
        ("Bemessungsleistung", f"{gn(k['p_design_gw'])} GW",
         f"Entladegrenze: Heizlast bei Auslegungstemperatur ({gn(k['q_design'], 1)} kWh/EW/Tag) + Warmwasser"),
        ("Ladeleistung", f"{gn(k['p_in_max_gw'])} GW genutzt / {gn(k['p_in_cap_gw'])} GW Grenze",
         f"Grenze: Heizlast bei {config.STORAGE_CHARGE_DESIGN_TEMP:.0f} °C + Warmwasser"),
        ("Entladeleistung", f"{gn(k['p_out_max_gw'])} GW genutzt / {gn(k['p_out_cap_gw'])} GW Grenze",
         "Ø Tagesleistung (GWh ÷ 24 h)"),
        ("Nicht deckbarer Wärmebedarf", f"{t(k['uncovered'])} TWh",
         "nicht ausreichend Überschuss, Speicherkapazität oder Entladeleistung; bleibt Residuallast"),
    ]
    if k["cyclic"]:
        rows += [
            ("Rechenweise", "stationäres Jahr (zirkulär)", "stationäres / zirkuläres Jahr: Inhalt zu Beginn = Inhalt am Ende"),
            ("Speicherinhalt am Jahreswechsel", f"{t(k['soc_start'])} TWh",
             "stationär: Inhalt zu Beginn = Inhalt am Ende"),
        ]
    else:
        rows += [
            ("Rechenweise", "linear", "Inhalt aus Vorjahr und Potenzial am Ende getrennt ausgewiesen"),
            ("Speicherinhalt aus Vorjahr", f"{t(k['prev_year'])} TWh", "notwendige Befüllung zu Beginn"),
            ("Speicherpotenzial am Ende", f"{t(k['potential_end'])} TWh", "mögliche Befüllung für künftigen Bedarf"),
            ("Jahr aus eigenem Überschuss deckbar", "ja" if k["self_sufficient"] else "nein",
             "Potenzial am Ende ≥ Inhalt aus Vorjahr"),
        ]
    st.dataframe(pd.DataFrame(rows, columns=["Kennzahl", "Wert", "Erläuterung"]), width="stretch", hide_index=True)
    st.caption(f"Kontrolle: Inhalt zu Beginn + IN − Verluste − OUT − Inhalt am Ende = "
               f"{gn((k['soc_start'] + k['in'] - k['loss'] - out_sum - k['soc_end']) / 1000.0, 3)} TWh (Soll 0).")



# ---------------------------------------------------------------------------
# Kopfzeile
# ---------------------------------------------------------------------------
col_title, col_logo = st.columns([8, 1])
with col_title:
    st.title("⚡ Energiewende-Simulator")
    st.markdown(texts.INTRO)
with col_logo:
    logo = APP_DIR / "solardoktor_logo.jpg"
    if logo.exists():
        b64 = base64.b64encode(logo.read_bytes()).decode()
        st.markdown(f'<div style="text-align:right;"><a href="https://www.solardoktor.de" target="_blank">'
                    f'<img src="data:image/jpeg;base64,{b64}" width="80" style="display:inline-block;"></a></div>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<div style="text-align:right;"><a href="https://www.solardoktor.de" target="_blank">'
                    '<b>SOLAR DOKTOR</b></a></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Seitenleiste: Zeitraum
# ---------------------------------------------------------------------------
today = date.today()
st.sidebar.header("Einstellungen")
# Feste Abschnitte (Reihenfolge in der Seitenleiste); befüllt werden sie in Programmreihenfolge.
box_period, box_build, box_sector, box_cost, box_temp, box_co2 = (st.sidebar.container() for _ in range(6))
sb = box_period
period_modes = [config.PERIOD_LAST_365, config.PERIOD_CALENDAR, config.PERIOD_WINTER]
period_mode = sb.radio("Zeitraum", period_modes, index=period_modes.index(config.DEFAULT_PERIOD_MODE))
if period_mode == config.PERIOD_CALENDAR:
    years = list(range(today.year - 1, config.FIRST_YEAR - 1, -1))   # nur abgeschlossene Jahre
    year = sb.selectbox("Jahr", years,
                        index=years.index(config.DEFAULT_YEAR) if config.DEFAULT_YEAR in years else 0)
    start_date, end_date = date(year, 1, 1), date(year, 12, 31)
elif period_mode == config.PERIOD_WINTER:
    winters = winter_options(today)                    # nur abgeschlossene Winterjahre (bis 30.6.)
    labels = [f"{y}/{(y + 1) % 100:02d}" for y in winters]
    default_label = f"{config.DEFAULT_WINTER}/{(config.DEFAULT_WINTER + 1) % 100:02d}"
    label = sb.selectbox("Winter", labels, index=labels.index(default_label) if default_label in labels else 0)
    year = winters[labels.index(label)]
    start_date, end_date = date(year, 7, 1), date(year + 1, 6, 30)
else:
    end_date = last_day_of_previous_month(today)       # bis Ende Vormonat (vollständige Daten)
    start_date = end_date - timedelta(days=364)
sb.caption(f"Zeitraum: {start_date:%d.%m.%Y} – {end_date:%d.%m.%Y}")

box_temp.markdown("**Temperatur (Heizung, Mobilität)**")
location = box_temp.selectbox("Standort Temperaturdaten", list(config.LOCATIONS),
                              index=list(config.LOCATIONS).index(config.DEFAULT_LOCATION))

# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------
timings: list[str] = []
with st.status("Lade Daten …", expanded=False) as status:
    status.update(label="Lade Erzeugung und Last (Energy-Charts) …")
    res, notes_p = timed("Erzeugung/Last", load_power, start_date, end_date, today)
    status.update(label="Lade installierte Leistung (Energy-Charts) …")
    res_i, notes_i = timed("Installierte Leistung", load_installed, today)
    status.update(label=f"Lade Temperaturen {location} (Open-Meteo) …")
    res_t, notes_t = timed("Temperaturen", load_temps, location, start_date, end_date, today)
    status.update(label="Daten geladen", state="complete")

if res is None:
    for n in notes_p:
        st.warning(n)
    st.error("Keine Energiedaten erhalten. Bitte in etwa einer Minute neu laden (Energy-Charts begrenzt "
             "die Zahl der Abrufe). Details zum Abruf stehen im Konsolenfenster bzw. im Cloud-Log.")
    st.stop()
raw, notes_p, src_p = res
installed, notes_i2, src_i = res_i if res_i else (pd.DataFrame(), [], [])
temps, notes_t2, src_t = res_t if res_t else (pd.DataFrame(columns=["date", "temp_mean"]), [], [])
notes = notes_p + notes_i + notes_i2 + notes_t + notes_t2
sources = src_p + src_i + src_t

inst_daily, inst_fallback = data.daily_installed(installed, raw["date"])
if inst_fallback:
    notes.append("Installierte Leistung nicht verfügbar – Ersatzwerte "
                 + ", ".join(f"{k} {v:.0f} GW" for k, v in config.FALLBACK_INSTALLED_GW.items()) + ".")
status_quo = {k: round(float(inst_daily[k].iloc[-1]), 1) for k in model.GEN_KEYS}   # am Zeitraumende
status_quo_today, status_quo_today_date = data.latest_installed(installed)           # neuester Monatswert

# ---------------------------------------------------------------------------
# Seitenleiste: Szenarien und Schalter
# ---------------------------------------------------------------------------
sb = box_build
sb.subheader("Ausbauszenarien")
scenario = sb.radio("Szenario", [*config.SCENARIOS, config.MANUAL], index=0)
if scenario != config.MANUAL:
    st.session_state["last_build_scenario"] = scenario
# Manuelle Werte: Vorbelegung mit dem zuletzt gewählten Szenario
base_scenario = st.session_state.get("last_build_scenario", config.STATUS_QUO_TODAY)
if config.SCENARIOS.get(base_scenario):
    sc = config.SCENARIOS[base_scenario]
else:
    sc = status_quo_today if base_scenario == config.STATUS_QUO_TODAY else status_quo
if scenario == config.STATUS_QUO_TODAY:
    sb.caption("Stand der installierten Leistung: "
               + (f"{status_quo_today_date:%m/%Y}" if status_quo_today_date is not None else "Ersatzwerte"))
with sb.expander("Manuelle Werte und Optionen", expanded=False):
    if scenario == config.MANUAL:
        t_solar = st.number_input("Ziel Solar (GWp)", 0.0, 600.0, float(sc["solar"]), 5.0)
        t_on = st.number_input("Ziel Wind Onshore (GW)", 0.0, 250.0, float(sc["wind_onshore"]), 5.0)
        t_off = st.number_input("Ziel Wind Offshore (GW)", 0.0, 100.0, float(sc["wind_offshore"]), 1.0)
    else:
        t_solar, t_on, t_off = sc["solar"], sc["wind_onshore"], sc["wind_offshore"]
        st.caption("Für eigene Ausbauwerte das Szenario „Manuelle Werte“ wählen (vorbelegt mit dem "
                   "zuletzt gewählten Szenario).")
    disable_monthly_scaling = st.checkbox(
        "Monatliche Skalierung aus", value=False,
        help="Verwendet die Ist-Erzeugung ohne Skalierung (Ziel = Ist). Zur Kontrolle gegenüber Energy-Charts.")
    disable_pumped_storage = st.checkbox(
        "Pumpspeicher/-Ladung aus", value=True,
        help="Schließt Pumpspeicher-Erzeugung und Pumpstrom aus. Eingeschaltet zählt der Saldo (≈ Speicherverluste).")

if disable_monthly_scaling:
    sb.caption(f"Skalierung aus – Ist-Leistung am Zeitraumende: Solar {gn(status_quo['solar'])} GWp | "
               f"Onshore {gn(status_quo['wind_onshore'])} GW | Offshore {gn(status_quo['wind_offshore'])} GW")
else:
    sb.caption(f"Solar: {gn(t_solar)} GWp | Wind Onshore: {gn(t_on)} GW | Offshore: {gn(t_off)} GW")

# ---------------------------------------------------------------------------
# Seitenleiste: Sektorenkopplung
# ---------------------------------------------------------------------------
sb = box_sector
sb.subheader("Sektorenkopplung – Zusatzlasten (über aktuellen Bestand hinaus)")
sector_scenario = sb.radio("Szenario Sektorenkopplung", [*config.SECTOR_SCENARIOS, config.MANUAL], index=0)
manual_sector = sector_scenario == config.MANUAL
if not manual_sector:
    st.session_state["last_sector_scenario"] = sector_scenario
# Manuelle Werte: Vorbelegung mit dem zuletzt gewählten Szenario
ssc = config.SECTOR_SCENARIOS[st.session_state.get("last_sector_scenario", config.SECTOR_STATUS_QUO)]
with sb.expander("Last-Skalierung und manuelle Werte", expanded=False):
    load_scale = st.slider("Last-Skalierung (%)", 50, 200, 100, 5) / 100.0
    if manual_sector:
        st.caption("Werte sind Gesamtzahlen; die Differenz zum aktuellen Bestand wird als Zusatzlast modelliert.")
        st.markdown("**Elektromobilität**")
        mio_ev = st.number_input(f"Elektro-PKW ({config.CURRENT_EV} … {config.MAX_EV}, gesamt) [Mio.]",
                                 config.CURRENT_EV, config.MAX_EV, int(ssc["ev"]), 1)
        mio_lkw = st.number_input(f"Elektro-LKW (0 … {config.MAX_LKW:.0f}, gesamt) [Mio.]", config.CURRENT_LKW,
                                  config.MAX_LKW, float(ssc["lkw"]), 0.1, format="%.1f")
        weekend_share = st.slider("Anteil Ladeenergie gezielt am Wochenende/Feiertag (0 … 50 %)", 0, 50,
                                  config.DEFAULT_WEEKEND_SHARE,
                                  help="Dieser Anteil wird auf Wochenenden und Feiertage gelegt, "
                                       "der Rest folgt dem saisonalen Profil über alle Tage.") / 100.0
        st.markdown("**Wärme**")
        mio_dhw = st.number_input(f"Elektrische Warmwasserbereitung ({config.CURRENT_DHW} … {config.MAX_HEAT_EW}, "
                                  f"gesamt) [Mio. Einwohner]", config.CURRENT_DHW, config.MAX_HEAT_EW,
                                  int(ssc["dhw"]), 1)
        mio_heat = st.number_input(f"Elektrische Heizung ({config.CURRENT_HEATING} … {config.MAX_HEAT_EW}, gesamt) "
                                   f"[Mio. Einwohner]", config.CURRENT_HEATING, config.MAX_HEAT_EW,
                                   int(ssc["heating"]), 1)
        st.markdown("**Elektrolyse**")
        electrolysis_gw = st.number_input(f"Elektrolyse (0 … {config.MAX_ELECTROLYSIS_GW}) [GW]", 0,
                                          config.MAX_ELECTROLYSIS_GW, int(ssc["electrolysis"]), 1,
                                          help="Elektrolyseure nutzen ausschließlich Erzeugungsüberschüsse "
                                               "(Superpower), höchstens Leistung × 24 h je Tag.")
    else:
        mio_ev, mio_lkw = ssc["ev"], ssc["lkw"]
        mio_dhw, mio_heat = ssc["dhw"], ssc["heating"]
        electrolysis_gw = int(ssc["electrolysis"])
        weekend_share = config.DEFAULT_WEEKEND_SHARE / 100.0
        st.caption("Für eigene Werte (E-Mobilität, Wärme, Elektrolyse) das Szenario „Manuelle Werte“ wählen "
                   "(vorbelegt mit dem zuletzt gewählten Szenario).")

# --- Heizung – Anteile: Speicheranteil immer sichtbar, übrige Parameter eingeklappt
sb.markdown("**Heizung – Anteile**")
sd = config.STORAGE_DEFAULTS
priority = sb.radio("Priorität bei Überschuss", [config.PRIORITY_ELYSE_FIRST, config.PRIORITY_STORAGE_FIRST], index=0,
                    help="Reihenfolge, in der Erzeugungsüberschüsse genutzt werden.")
storage_first = priority == config.PRIORITY_STORAGE_FIRST
st_share = sb.slider("Anteil Langzeit-Wärmespeicher (%)", 0, 100, sd["share"], 5,
                     help="Bezogen auf die zusätzlichen Heizungs-Einwohner (über dem Bestand). Diese Haushalte "
                          "heizen und bereiten Warmwasser direktelektrisch über einen Langzeit-Wärmespeicher.")
with sb.expander("Wärmepumpen-Anteile" + (" und Speicherparameter" if st_share > 0 else ""), expanded=False):
    hp_air = st.slider("Anteil Luft-Wasser-WP (% des Rests)", 0, 100, 60)
    hp_water = st.slider("Anteil Wasser-Wasser-/Sole-WP (% des Rests)", 0, 100, 10)
    if hp_air + hp_water > 100:
        st.warning("Summe der WP-Anteile > 100 % – Sole-WP wird auf den Rest begrenzt.")
        hp_water = 100 - hp_air
    st.caption(f"Direktelektrisch: {100 - hp_air - hp_water} % des Rests"
               + (f" (Rest = {100 - st_share} % ohne Langzeit-Wärmespeicher)" if st_share > 0 else ""))
    if st_share > 0:
        st_loss = st.slider("Speicherverlust (% des Inhalts je Tag)", 0.0, 5.0, float(sd["loss_pct"]), 0.1,
                            format="%.1f")
        st_days = st.slider("Bemessungs-Speicherdauer (Tage)", 0, config.MAX_STORAGE_DAYS, sd["days"], 1,
                            help="Begrenzt die Speicherkapazität: Bemessungsleistung × Bemessungs-Speicherdauer. "
                                 "Wie lange einzelne Energiemengen im Speicher bleiben, ist nicht begrenzt.")
        st_tdesign = st.slider("Auslegungstemperatur (°C)", -20, 5, sd["design_temp"], 1,
                               help="Bis zu dieser Tagesmitteltemperatur deckt der Speicher die volle Heizlast; "
                                    "an kälteren Tagen kommt der Rest aus dem Netz.")
        st_cyclic = st.checkbox("Speicher als stationäres Jahr rechnen", value=True,
                                help="Wie bei den Dunkelflauten: Defizite am Zeitraumbeginn werden aus Überschüssen "
                                     "am Zeitraumende gedeckt (Speicherinhalt Beginn = Ende). Aus: Inhalt aus Vorjahr "
                                     "und Speicherpotenzial am Ende werden getrennt ausgewiesen.")
    else:
        st_loss, st_days, st_tdesign = float(sd["loss_pct"]), sd["days"], sd["design_temp"]
        st_cyclic = True
sb.caption(f"Luft-WP {hp_air} % · Sole-WP {hp_water} % · direkt {100 - hp_air - hp_water} % des Rests"
           + (f" · Speicher: Verlust {gn(st_loss, 1)} %/Tag, Bemessung {st_days} Tage, Auslegung {st_tdesign} °C"
              if st_share > 0 else ""))

add_dhw = max(0.0, float(mio_dhw) - config.CURRENT_DHW)
add_heat = max(0.0, float(mio_heat) - config.CURRENT_HEATING)
if st_share > 0 and add_heat <= 0:
    sb.warning("Langzeit-Wärmespeicher ohne Wirkung: keine zusätzlichen Heizungs-Einwohner über dem Bestand "
               f"({config.CURRENT_HEATING} Mio.). Bitte Szenario 2030/2045 oder höhere Heizungs-Eingabe wählen.")
if st_share > 0:
    n_s = add_heat * st_share / 100.0
    sb.caption(f"Speicher-Einwohner: {gn(n_s)} Mio. EW ({st_share} % von {gn(add_heat, 0)} Mio. zusätzlichen "
               f"Heizungs-EW); Warmwasser mindestens {gn(config.CURRENT_DHW + n_s)} Mio. EW")
    if add_dhw + 1e-9 < n_s:
        sb.warning(f"Warmwasser-Eingabe zu klein: mindestens {gn(config.CURRENT_DHW + n_s)} Mio. EW nötig.")

# --- Elektrolyse (nachrichtlich; Eingabe unter „Manuelle Werte“)
sb.markdown("**Elektrolyse**")
sb.caption(f"Max. Tagesbedarf Elektrolyse: {gn(electrolysis_gw, 0)} GW × 24 h = {gn(electrolysis_gw * 24, 0)} GWh")

# --- nachrichtlich
sb.caption(f"E-PKW: {gn(mio_ev, 0)} Mio | E-LKW: {gn(mio_lkw)} Mio | "
           f"E-Warmwasser: {gn(mio_dhw, 0)} Mio EW | E-Heizung: {gn(mio_heat, 0)} Mio EW | "
           f"Elektrolyse: {gn(electrolysis_gw, 0)} GW")

add_ev = max(0.0, float(mio_ev) - config.CURRENT_EV)
add_lkw = max(0.0, round(float(mio_lkw) - config.CURRENT_LKW, 1))
sb.caption(f"Zusatzverbrauch (über Stand von 2026): +{gn(add_ev)} Mio PKW, +{gn(add_lkw)} Mio LKW, "
           f"+{gn(add_dhw, 0)} Mio EW WW, +{gn(add_heat, 0)} Mio EW Heizung")

sb = box_cost
if st_share > 0:
    with sb.expander("Kostenparameter (Kostenvergleich)", expanded=False):
        cd = costs.CostParams()
        cost_params = costs.CostParams(
            wp_eur_per_kw=st.number_input("Wärmepumpe [€/kW(el)]", 0, 50_000, int(cd.wp_eur_per_kw), 500),
            residual_eur_per_kw=st.number_input("Residualleistung Erdgas-KW [€/kW]", 0, 10_000,
                                                int(cd.residual_eur_per_kw), 100),
            storage_eur_per_kwh=st.number_input("Langzeitspeicher HTS [€/kWh]", 0.0, 500.0, cd.storage_eur_per_kwh,
                                                1.0, format="%.0f"),
            price_gas_ct=st.number_input("Strompreis Erdgasstrom [ct/kWh]", 0.0, 100.0, cd.price_gas_ct, 0.5,
                                         format="%.1f"),
            price_surplus_ct=st.number_input("Strompreis Superpower [ct/kWh]", 0.0, 50.0, cd.price_surplus_ct, 0.5,
                                             format="%.1f"),
            fee_inflexible_ct=st.number_input("Netzentgelte/Umlagen Direktheizung, unflexibel [ct/kWh]", 0.0, 50.0,
                                              cd.fee_inflexible_ct, 0.5, format="%.1f"),
            fee_flexible_ct=st.number_input("Netzentgelte/Umlagen Speicherheizung, flexibel [ct/kWh]", 0.0, 50.0,
                                            cd.fee_flexible_ct, 0.5, format="%.1f"),
            life_wp_years=st.number_input("Nutzungsdauer Wärmepumpen [Jahre]", 5, 80, cd.life_wp_years, 1),
            life_res_years=st.number_input("Nutzungsdauer Erdgaskraftwerke [Jahre]", 5, 80, cd.life_res_years, 1),
            life_sto_years=st.number_input("Nutzungsdauer Langzeit-Wärmespeicher [Jahre]", 5, 80,
                                           cd.life_sto_years, 1),
            interest_pct=st.number_input("Kalkulationszins [%]", 0.0, 15.0, cd.interest_pct, 0.5, format="%.1f"),
        )
else:
    cost_params = costs.CostParams()

sb = box_co2
sb.subheader("CO₂")
co2_specific = sb.number_input("Spezifische CO₂-Intensität Residual (g/kWh)", 200, 1500,
                               config.CO2_DEFAULT_G_PER_KWH, 50)

# ---------------------------------------------------------------------------
# Berechnung
# ---------------------------------------------------------------------------
hgt_ref = None
if add_heat > 0:      # vorberechnete Referenz-Heizgradtage (data/hgt_reference.json)
    hgt_ref, notes_h = load_hgt_reference(location)
    notes += notes_h
params = model.Params(
    scaling_enabled=not disable_monthly_scaling,
    target_solar=float(t_solar), target_wind_onshore=float(t_on), target_wind_offshore=float(t_off),
    include_pumped_storage=not disable_pumped_storage, load_scale=load_scale,
    add_ev_mio=add_ev, add_lkw_mio=add_lkw, weekend_share=weekend_share,
    add_dhw_mio=add_dhw, add_heating_mio=add_heat,
    share_air_hp=hp_air / 100.0, share_water_hp=hp_water / 100.0,
    co2_specific=float(co2_specific), hgt_reference=hgt_ref,
    electrolysis_gw=float(electrolysis_gw),
    storage_share=st_share / 100.0, storage_loss=st_loss / 100.0, storage_days=int(st_days),
    design_temp=float(st_tdesign), storage_cyclic=bool(st_cyclic), storage_first=storage_first,
)
holiday_dates = frozenset(holidays.Germany(years=range(start_date.year, end_date.year + 1)).keys())
try:
    df0, S0, ST = run_model(raw, inst_daily, temps, holiday_dates, params)
except KeyError as e:
    st.error(f"{e} Verfügbare Serien: {', '.join(c for c in raw.columns if c not in ('date', 'coverage'))}")
    st.stop()

for n in notes:
    st.warning(n)

# Hauptausgabe: mit Speicher (Z2), sofern aktiv; sonst Referenz (Z0)
storage_on = ST.active
df, S = (ST.z2, ST.summary_z2) if storage_on else (df0, S0)

# ---------------------------------------------------------------------------
# Grafik
# ---------------------------------------------------------------------------
st.subheader("Tagesenergie: Skalierte Erzeugung und Last")
period_str = f"{start_date:%d.%m.%Y} – {end_date:%d.%m.%Y}"
if params.scaling_enabled:
    gen_txt = (f"Gesamt-Nettostromerzeugung bei {t_solar:.0f} GWp Solar und "
               f"{t_on + t_off:.0f} GW Wind")
else:
    gen_txt = (f"Gesamt-Nettostromerzeugung mit Ist-Leistung, unskaliert (am Zeitraumende "
               f"{status_quo['solar']:.0f} GWp Solar, {status_quo['wind_onshore'] + status_quo['wind_offshore']:.0f} GW Wind)")
load_parts = [f"e-PKW ({gn(mio_ev, 0)} Mio.)", f"e-LKW ({gn(mio_lkw)} Mio.)",
              f"e-Warmwasser ({gn(mio_dhw, 0)} Mio. EW)", f"e-Heizung ({gn(mio_heat, 0)} Mio. EW)"]
surplus_parts = []                                     # Reihenfolge nach Priorität bei Überschuss
if electrolysis_gw > 0:
    surplus_parts.append(f"Elektrolyse ({gn(electrolysis_gw, 0)} GW)")
if storage_on:
    surplus_parts.insert(0 if storage_first else len(surplus_parts),
                         f"Langzeit-Wärmespeicher ({st_share} %, {gn(ST.kpi['n_s'])} Mio. EW)")
subtitle = f"{gen_txt}<br>Gesamt-Last aus Basis-Last, " + ", ".join(load_parts + surplus_parts)
fig = charts.build_figure(df, start_date, end_date,
                          f"Tagesbilanz von Stromerzeugung und Verbrauch in Deutschland  {period_str}", subtitle,
                          storage_first=storage_first and storage_on)
st.plotly_chart(fig, width="stretch")
if storage_on:
    st.caption("Speicher OUT (heller, ganz oben): Wärmebedarf, der aus dem Langzeit-Wärmespeicher gedeckt wird "
               "(kein Netzbezug). Speicher IN: eingespeicherter Überschuss. Hover: Netzlast = Netzbezug.")

if df["temp_mean"].notna().any():
    st.subheader("Temperatur und Langzeit-Wärmespeicher" if storage_on else f"Tagesmitteltemperatur ({location})")
    st.plotly_chart(charts.build_temperature_storage(df, start_date, end_date, float(st_tdesign)), width="stretch")

# ---------------------------------------------------------------------------
# Geordnete Jahresdauerlinie mit Vergleich zum vorhergehenden Berechnungslauf
# (Zwischenspeicher: st.session_state – je Browser-Sitzung, nicht zwischen Nutzern geteilt)
# ---------------------------------------------------------------------------
st.subheader("Geordnete Jahresdauerlinie: Last − EE")
run_signature = (replace(params, co2_specific=0.0), start_date, end_date, location)   # CO₂ ändert die Kurve nicht
run_label = describe_run(params, start_date, end_date, location, mio_ev, mio_lkw, mio_dhw, mio_heat)
current_run = {"signature": run_signature, "label": run_label, "curve": model.duration_curve(df)}
stored = st.session_state.get("dl_current")
if stored is None or stored["signature"] != run_signature:
    if stored is not None:
        st.session_state["dl_previous"] = stored          # bisheriger Lauf wird zum Vergleich
    st.session_state["dl_current"] = current_run
previous_run = st.session_state.get("dl_previous")

st.plotly_chart(charts.build_duration_curve(current_run, previous_run,
                                            model.duration_curve(df0) if storage_on else None), width="stretch")
col_dl1, col_dl2 = st.columns([4, 1])
col_dl1.caption("Mittlere Tagesleistung = Tagesenergie / 24 h, absteigend sortiert. Positive Werte: Residuallast "
                "(EE-Defizit), negative Werte: Superpower (EE-Überschuss). Verglichen wird mit dem vorhergehenden "
                "Berechnungslauf mit anderen Parametern in dieser Browser-Sitzung.")
if previous_run is not None and col_dl2.button("Vergleich zurücksetzen"):
    st.session_state.pop("dl_previous", None)
    st.rerun()

# ---------------------------------------------------------------------------
# Jahresbilanz
# ---------------------------------------------------------------------------
st.subheader("Jahresbilanz")


def both(key: str, decimals: int = 1, scale: float = 1000.0, src: str = "sums_gwh") -> str:
    """Wert mit Speicher; abweichender Wert ohne Speicher (Z0) in Klammern."""
    get = (lambda sx: sx[src][key]) if src else (lambda sx: sx[key])
    a = gn(get(S) / scale, decimals)
    if not storage_on:
        return a
    b = gn(get(S0) / scale, decimals) if key in (S0[src] if src else S0) else "–"
    return a if a == b else f"{a} ({b})"


gen_rows = [("Solar", "solar_scaled"), ("Wind Onshore", "wind_onshore_scaled"),
            ("Wind Offshore", "wind_offshore_scaled"), ("Sonstige EE", "other_ee")]
sfx = " (Netzbezug + Speicherentladung)" if storage_on else ""
load_rows = [("Basis-Last", "base_load_gwh"), ("Mobilität", "mobility_gwh"), ("Warmwasser" + sfx, "dhw_gwh"),
             ("Heizung" + sfx, "heating_gwh"), ("Elektrolyse", "electrolysis_gwh")]
if storage_on:
    load_rows.append(("Speicher IN", "storage_in_gwh"))
n_fill = len(load_rows) - len(gen_rows)
left = [n for n, _ in gen_rows] + [""] * n_fill + ["Summe EE", "Residualerzeugung"]
left_v = [both(k) for _, k in gen_rows] + [""] * n_fill + [both("ee_day"), both("residual_day")]
right = [n for n, _ in load_rows]
out_col = {"dhw_gwh": "out_dhw", "heating_gwh": "out_heat"}


def load_value(k: str) -> str:
    if not storage_on:
        return both(k)
    if k in out_col:                                   # Netzbezug + Speicherentladung (ohne Speicher)
        return (f"{gn(S['sums_gwh'][k] / 1000.0)} + {gn(ST.kpi[out_col[k]] / 1000.0)} "
                f"({gn(S0['sums_gwh'][k] / 1000.0)})")
    return both(k) if k in S0["sums_gwh"] else gn(S["sums_gwh"][k] / 1000.0)


right_v = [load_value(k) for _, k in load_rows]
co2_v = [both(k, 0, 1.0, "co2_by_category") if k in S0["co2_by_category"] or not storage_on
         else gn(S["co2_by_category"][k], 0) for _, k in load_rows]
right += ["Summe Verbrauch", "Superpower"]
right_v += [both("total_load_gwh"), both("superpower_day")]
co2_v += [both("co2_mean", 0, 1.0, None), "0"]
summary = pd.DataFrame({"Erzeugung": left, "Wert [TWh]": left_v, "(Mehr-) Verbrauch": right,
                        "Wert [TWh] ": right_v, "CO₂ [g/kWh]": co2_v})
st.dataframe(summary, width="stretch", hide_index=True)
if storage_on:
    st.caption("Werte mit Langzeit-Wärmespeicher; in Klammern abweichende Werte ohne Speicher (Referenz: "
               "Speicher-Haushalte mit Wärmepumpen-Mix). Warmwasser und Heizung: Netzbezug + Speicherentladung "
               "(die Speicherentladung ist kein Netzbezug und zählt nicht zur Summe Verbrauch). Der Verbrauch steigt "
               "durch die direktelektrische Beladung (statt Wärmepumpen) und die Speicherverluste; Details in der "
               "Speicherbilanz.")
if electrolysis_gw > 0:
    elyse_gwh = S["sums_gwh"]["electrolysis_gwh"]
    st.caption(f"Elektrolyse (nachrichtlich): {gn(elyse_gwh / 1000)} TWh, "
               f"{gn(elyse_gwh / electrolysis_gw, 0)} Volllaststunden bei {gn(electrolysis_gw, 0)} GW "
               f"(Energie ÷ Leistung; Maximum {gn(len(df) * 24, 0)} h)")

if st_share > 0:
    render_storage_balance(ST, S0)
    if ST.active:
        render_cost_comparison(costs.compute_costs(df0, ST, params, cost_params,
                                                   S0["peak_residual"][0], ST.summary_z2["peak_residual"][0]),
                               cost_params)

def period_line(sx: dict, kind: str) -> str:
    if kind == "sp":
        gw, dt = sx["peak_superpower"]
        p, days = (sx["surplus_periods"][0] if sx["surplus_periods"] else None), sx["surplus_days"]
    else:
        gw, dt = sx["peak_residual"]
        p, days = (sx["deficit_periods"][0] if sx["deficit_periods"] else None), sx["deficit_days"]
    return (f"{fmt_period(p)} / {days} Tage / {gn(gw)} GW ({dt:%d.%m.%Y})" if dt is not None
            else f"{fmt_period(p)} / 0 Tage / –")


prefix = "mit Speicher: " if storage_on else ""
result_line("Längste Superpower / Tage mit Superpower / Ø-Leistung am Tag mit größtem Überschuss",
            prefix + period_line(S, "sp"), note=("ohne Speicher: " + period_line(S0, "sp")) if storage_on else None)
result_line("Längste Dunkelflaute / Tage mit EE-Defizit / Ø-Residualleistung am Tag mit größtem Defizit",
            prefix + period_line(S, "df"), note=("ohne Speicher: " + period_line(S0, "df")) if storage_on else None)

S_top = S
if storage_on:
    choice = st.radio("Top-10 Dunkelflauten", ["mit Speicher", "ohne Speicher"], horizontal=True)
    S_top = S if choice == "mit Speicher" else S0
if S_top["deficit_periods"]:
    st.subheader("Top-10 Dunkelflauten" + ((" – " + choice) if storage_on else ""))
    top = pd.DataFrame([{
        "Nr": i, "Start": f"{p.start:%d.%m.%Y}", "Ende": f"{p.end:%d.%m.%Y}" + (" ↻" if p.wraps else ""),
        "Dauer [Tage]": p.days, "Gesamtdefizit [TWh]": gn(p.energy_twh),
    } for i, p in enumerate(S_top["deficit_periods"][:10], 1)])
    st.dataframe(top, width="stretch", hide_index=True, column_config={
        "Nr": st.column_config.NumberColumn(width="small"),
        "Ende": st.column_config.TextColumn(help="↻ = läuft über die Zeitraumgrenze (stationäres Jahr)"),
    })

st.caption(texts.DUNKELFLAUTE_DEFINITION)

c1, c2, c3 = st.columns(3)
result_line("EE-Anteil am Verbrauch", gn(S["ee_share"] * 100) + " %", target=c1,
            note=f"ohne Speicher: {gn(S0['ee_share'] * 100)} %" if storage_on else None)
result_line(f"Ø Temperatur ({location})",
            gn(S["avg_temp"]) + " °C" if S["avg_temp"] is not None else "n/a", target=c2)
result_line("Heizgradtage (15 °C)", gn(S["hgt"], 0) if S["hgt"] is not None else "n/a",
            note=f"Referenz (Normaljahr {config.HGT_REFERENCE_YEARS[0]}–{config.HGT_REFERENCE_YEARS[1]}): "
                 + (gn(hgt_ref, 0) if hgt_ref else "n/a"), target=c3)

# ---------------------------------------------------------------------------
# Erläuterungen, Debug, Annahmen, Spenden
# ---------------------------------------------------------------------------
with st.expander("Erläuterung zur Darstellung und Annahmen", expanded=True):
    st.markdown(texts.EXPLANATION)

if storage_on:
    with st.expander("Vergleich der Rechenzustände (Langzeit-Wärmespeicher)", expanded=False):
        render_state_comparison(ST, S0)

with st.expander("Debug: Datenbasis und tägliche Differenz EE – Verbrauch", expanded=False):
    st.markdown(texts.DEBUG_COLUMNS)
    st.markdown("**Datenquellen und Abrufzeitpunkte**")
    st.markdown("\n".join(f"- {x}" for x in sources) or "–")
    st.markdown("**Ladezeiten dieses Aufrufs:** " + " | ".join(timings))
    series = [c for c in raw.columns if c not in ("date", "coverage")]
    st.markdown("**Energy-Charts-Serien im Datensatz:** " + ", ".join(f"`{c}`" for c in series))
    used = {config.LOAD_SERIES, *model.GEN_SERIES.values(), *config.OTHER_EE_SERIES, *config.PUMPED_STORAGE_SERIES}
    missing = sorted(used - set(series))
    if missing:
        st.markdown("**Erwartet, aber nicht vorhanden:** " + ", ".join(f"`{c}`" for c in missing))
    incomplete = df.loc[df["coverage"] < data.COVERAGE_OK, ["date", "coverage"]]
    st.markdown(f"**Tage mit unvollständigen Daten:** {len(incomplete)}")
    if len(incomplete):
        st.dataframe(incomplete.assign(date=incomplete["date"].dt.strftime("%d.%m.%Y")), hide_index=True)
    debug_cols = ["date", "coverage", "temp_mean", "ee_day", "total_load_gwh", "ee_minus_load",
                  "superpower_day", "residual_day", "electrolysis_gwh", "other_ee", "pumped_storage_net",
                  *[f"installed_{k}" for k in model.GEN_KEYS], *[f"scale_{k}" for k in model.GEN_KEYS]]
    debug_df = df[debug_cols].copy()
    debug_df["date"] = debug_df["date"].dt.strftime("%d.%m.%Y")
    st.dataframe(debug_df, width="stretch", hide_index=True)
    export_df = df
    if ST.active:
        st_cols = ["storage_ww_s", "storage_h_s", "extra_dhw_gwh", "extra_heat_gwh", "storage_surplus_avail",
                   "storage_residual_z1", "storage_in_gwh", "storage_out_dhw_gwh", "storage_out_heat_gwh",
                   "storage_uncovered_gwh", "storage_loss_gwh", "storage_soc_gwh", "storage_cap_out_gwh"]
        st.markdown("**Speicher-Tageswerte (GWh)** – U = `storage_surplus_avail`, R = `storage_residual_z1`, "
                    "IN, OUT, Verlust, Inhalt (SOC) am Tagesende")
        st_df = ST.z2[["date", *st_cols]].copy()
        st_df["date"] = st_df["date"].dt.strftime("%d.%m.%Y")
        st.dataframe(st_df, width="stretch", hide_index=True)
        export_df = df
    st.download_button("Alle Tageswerte als CSV",
                       export_df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                       file_name=f"energiewende_{start_date}_{end_date}.csv", mime="text/csv")

st.markdown("---")
st.markdown("Datenquelle: [Energy-Charts.info](https://www.energy-charts.info) | Temperaturen: Open-Meteo")

with st.expander("Annahmen und Dokumentation der Berechnungen", expanded=False):
    st.markdown(texts.assumptions_markdown())

with st.expander("Unterstütze meine Arbeit", expanded=False):
    st.markdown(texts.DONATION)
    col_w, col_p = st.columns(2)
    for col, name, cap in ((col_w, "QR_wero.png", "Wero"), (col_p, "QR_paypal.png", "PayPal")):
        if (APP_DIR / name).exists():
            col.image(str(APP_DIR / name), width=180, caption=cap)
    st.caption("Dezent, ohne Tracking – einfach als Hinweis.")
