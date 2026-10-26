"""
Energiewende-Simulator (Solar Doktor)
Streamlit-App für eine skalierte Tagesenergie-Übersicht auf Basis von Energy-Charts-Daten.
Annahme: Intra-Day-Abweichungen durch Speicher und Flexibilitäten ausgleichbar.

Start:  streamlit run SDR_TOOL_Energiewende-Simulator.py
"""

import base64
from datetime import date, timedelta
from pathlib import Path

import holidays
import pandas as pd
import streamlit as st

from energiewende import charts, config, data, model, texts
from energiewende.model import german_number as gn
from energiewende.store import last_day_of_previous_month

st.set_page_config(page_title="Energiewende-Simulator", page_icon="⚡", layout="wide",
                   initial_sidebar_state="expanded")
APP_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Gecachte Lade- und Rechenfunktionen
# (``today`` ist Teil des Cache-Schlüssels → spätestens täglich neu bewertet)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_power(start: date, end: date, today: date):
    r = data.load_power_period(start, end, today)
    return r.df, r.notes, r.sources


@st.cache_data(ttl=3600, show_spinner=False)
def load_installed(today: date):
    r = data.load_installed(today)
    return r.df, r.notes, r.sources


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_temps(location: str, start: date, end: date, today: date):
    r = data.load_temperatures(location, start, end, today)
    return r.df, r.notes, r.sources


@st.cache_data(show_spinner=False)
def load_hgt_reference(location: str):
    return data.load_hgt_reference(location)


@st.cache_data(show_spinner=False, max_entries=64)
def run_model(raw, inst_daily, temps, holiday_dates, params: model.Params):
    df = model.compute_daily(raw, inst_daily, temps, holiday_dates, params)
    return df, model.summarize(df, params, cyclic=True)


def fmt_period(p: model.Period | None) -> str:
    if p is None:
        return "0 Tage"
    wrap = " ↻" if p.wraps else ""
    return f"{p.days} Tage ({p.start:%d.%m.%Y} bis {p.end:%d.%m.%Y}{wrap})"


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
sb = st.sidebar
sb.header("Einstellungen")
period_mode = sb.radio("Zeitraum", ["Letzte 365 Tage", "Kalenderjahr"], index=0)
if period_mode == "Kalenderjahr":
    years = list(range(today.year - 1, config.FIRST_YEAR - 1, -1))   # nur abgeschlossene Jahre
    year = sb.selectbox("Jahr", years, index=0)
    start_date, end_date = date(year, 1, 1), date(year, 12, 31)
else:
    end_date = last_day_of_previous_month(today)       # bis Ende Vormonat (vollständige Daten)
    start_date = end_date - timedelta(days=364)
sb.caption(f"Zeitraum: {start_date:%d.%m.%Y} – {end_date:%d.%m.%Y}")

load_scale = sb.slider("Last-Skalierung (%)", 50, 200, 100, 5) / 100.0

sb.markdown("**Temperatur (Heizung, Mobilität)**")
location = sb.selectbox("Standort Temperaturdaten", list(config.LOCATIONS),
                        index=list(config.LOCATIONS).index(config.DEFAULT_LOCATION))

# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------
with st.spinner("Lade Daten …"):
    raw, notes_p, src_p = load_power(start_date, end_date, today)
    installed, notes_i, src_i = load_installed(today)
    temps, notes_t, src_t = load_temps(location, start_date, end_date, today)
    hgt_ref, notes_h = load_hgt_reference(location)
notes = notes_p + notes_i + notes_t + notes_h
sources = src_p + src_i + src_t

if raw.empty or raw["coverage"].sum() == 0:
    for n in notes:
        st.warning(n)
    st.error("Keine Energiedaten erhalten. Bitte später erneut versuchen.")
    st.stop()

inst_daily, inst_fallback = data.daily_installed(installed, raw["date"])
if inst_fallback:
    notes.append("Installierte Leistung nicht verfügbar – Ersatzwerte "
                 + ", ".join(f"{k} {v:.0f} GW" for k, v in config.FALLBACK_INSTALLED_GW.items()) + ".")
status_quo = {k: round(float(inst_daily[k].iloc[-1]), 1) for k in model.GEN_KEYS}

# ---------------------------------------------------------------------------
# Seitenleiste: Szenarien und Schalter
# ---------------------------------------------------------------------------
sb.subheader("Ausbauszenarien")
scenario = sb.radio("Szenario", list(config.SCENARIOS), index=0)
sc = config.SCENARIOS[scenario] or status_quo

use_manual = sb.checkbox("Manuelle Ziele verwenden", value=False)
disable_monthly_scaling = sb.checkbox(
    "Monatliche Skalierung aus", value=False,
    help="Verwendet die Ist-Erzeugung ohne Skalierung (Ziel = Ist). Zur Kontrolle gegenüber Energy-Charts.")
disable_pumped_storage = sb.checkbox(
    "Pumpspeicher/-Ladung aus", value=True,
    help="Schließt Pumpspeicher-Erzeugung und Pumpstrom aus. Eingeschaltet zählt der Saldo (≈ Speicherverluste).")
if use_manual:
    t_solar = sb.number_input("Ziel Solar (GWp)", 0.0, 600.0, float(sc["solar"]), 5.0)
    t_on = sb.number_input("Ziel Wind Onshore (GW)", 0.0, 250.0, float(sc["wind_onshore"]), 5.0)
    t_off = sb.number_input("Ziel Wind Offshore (GW)", 0.0, 100.0, float(sc["wind_offshore"]), 1.0)
else:
    t_solar, t_on, t_off = sc["solar"], sc["wind_onshore"], sc["wind_offshore"]

if disable_monthly_scaling:
    sb.caption(f"Skalierung aus – Ist-Leistung am Zeitraumende: Solar {gn(status_quo['solar'])} GWp | "
               f"Onshore {gn(status_quo['wind_onshore'])} GW | Offshore {gn(status_quo['wind_offshore'])} GW")
else:
    sb.caption(f"Solar: {gn(t_solar)} GWp | Wind Onshore: {gn(t_on)} GW | Offshore: {gn(t_off)} GW")

# ---------------------------------------------------------------------------
# Seitenleiste: Sektorenkopplung
# ---------------------------------------------------------------------------
sb.subheader("Sektorenkopplung – Zusatzlasten (über aktuellen Bestand hinaus)")
sb.caption("Defaults entsprechen ungefähr dem aktuellen Bestand. Die Eingabe ist der Gesamtzielwert; "
           "die Differenz zum Bestand wird als Zusatzlast modelliert.")

sb.markdown("**Elektromobilität**")
mio_ev = sb.number_input("Millionen Elektro-PKW (2 … 40, gesamt)", config.CURRENT_EV, 40, config.CURRENT_EV, 1)
mio_lkw = sb.number_input("Millionen Elektro-LKW (0 … 4, gesamt)", config.CURRENT_LKW, 4.0, config.CURRENT_LKW,
                          0.1, format="%.1f")
weekend_share = sb.slider("Anteil Ladeenergie gezielt am Wochenende/Feiertag (0 … 50 %)", 0, 50, 20,
                          help="Dieser Anteil wird auf Wochenenden und Feiertage gelegt, "
                               "der Rest folgt dem saisonalen Profil über alle Tage.") / 100.0

sb.markdown("**Wärme**")
mio_dhw = sb.number_input("Millionen Einwohner elektrische Warmwasserbereitung (15 … 80, gesamt)",
                          config.CURRENT_DHW, 80, config.CURRENT_DHW, 1)
mio_heat = sb.number_input("Millionen Einwohner elektrische Heizung (5 … 80, gesamt)",
                           config.CURRENT_HEATING, 80, config.CURRENT_HEATING, 1)
sb.markdown("**Heizung – Anteile (Summe ≤ 100 %)**")
hp_air = sb.slider("Anteil Luft-Wasser-WP (%)", 0, 100, 60)
hp_water = sb.slider("Anteil Wasser-Wasser-/Sole-WP (%)", 0, 100, 10)
if hp_air + hp_water > 100:
    sb.warning("Summe der WP-Anteile > 100 % – Sole-WP wird auf den Rest begrenzt.")
    hp_water = 100 - hp_air
sb.caption(f"Direktelektrisch: {100 - hp_air - hp_water} %")

add_ev = max(0.0, float(mio_ev) - config.CURRENT_EV)
add_lkw = max(0.0, round(float(mio_lkw) - config.CURRENT_LKW, 1))
add_dhw = max(0.0, float(mio_dhw) - config.CURRENT_DHW)
add_heat = max(0.0, float(mio_heat) - config.CURRENT_HEATING)
sb.caption(f"Zusatzverbrauch (über Stand von 2026): +{gn(add_ev)} Mio PKW, +{gn(add_lkw)} Mio LKW, "
           f"+{gn(add_dhw, 0)} Mio EW WW, +{gn(add_heat, 0)} Mio EW Heizung")

sb.subheader("CO₂")
co2_specific = sb.number_input("Spezifische CO₂-Intensität Residual (g/kWh)", 200, 1500,
                               config.CO2_DEFAULT_G_PER_KWH, 50)

# ---------------------------------------------------------------------------
# Berechnung
# ---------------------------------------------------------------------------
params = model.Params(
    scaling_enabled=not disable_monthly_scaling,
    target_solar=float(t_solar), target_wind_onshore=float(t_on), target_wind_offshore=float(t_off),
    include_pumped_storage=not disable_pumped_storage, load_scale=load_scale,
    add_ev_mio=add_ev, add_lkw_mio=add_lkw, weekend_share=weekend_share,
    add_dhw_mio=add_dhw, add_heating_mio=add_heat,
    share_air_hp=hp_air / 100.0, share_water_hp=hp_water / 100.0,
    co2_specific=float(co2_specific), hgt_reference=hgt_ref,
)
holiday_dates = frozenset(holidays.Germany(years=range(start_date.year, end_date.year + 1)).keys())
try:
    df, S = run_model(raw, inst_daily, temps, holiday_dates, params)
except KeyError as e:
    st.error(f"{e} Verfügbare Serien: {', '.join(c for c in raw.columns if c not in ('date', 'coverage'))}")
    st.stop()

for n in notes:
    st.warning(n)

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
subtitle = (f"{gen_txt}<br>Gesamt-Last aus Basis-Last, e-PKW ({mio_ev:.1f} Mio.), e-LKW ({mio_lkw:.1f} Mio.), "
            f"e-Warmwasser ({mio_dhw:.0f} Mio. EW), e-Heizung ({mio_heat:.0f} Mio. EW)")
fig = charts.build_figure(df, start_date, end_date,
                          f"Tagesbilanz von Stromerzeugung und Verbrauch in Deutschland  {period_str}", subtitle)
st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# Jahresbilanz
# ---------------------------------------------------------------------------
st.subheader("Jahresbilanz")
s = S["sums_gwh"]
twh = {k: v / 1000.0 for k, v in s.items()}
co2c = S["co2_by_category"]
summary = pd.DataFrame({
    "Erzeugung": ["Solar", "Wind Onshore", "Wind Offshore", "Sonstige EE", "Summe EE", "Residualerzeugung"],
    "Wert [TWh]": [gn(twh[k]) for k in ("solar_scaled", "wind_onshore_scaled", "wind_offshore_scaled",
                                         "other_ee", "ee_day", "residual_day")],
    "(Mehr-) Verbrauch": ["Basis-Last", "Mobilität", "Warmwasser", "Heizung", "Summe Verbrauch", "Superpower"],
    "Wert [TWh] ": [gn(twh[k]) for k in (*model.LOAD_COLS, "total_load_gwh", "superpower_day")],
    "CO₂ [g/kWh]": [gn(co2c[k], 0) for k in model.LOAD_COLS] + [gn(S["co2_mean"], 0), "0"],
})
st.dataframe(summary, width="stretch", hide_index=True)

sp_gw, sp_date = S["peak_superpower"]
res_gw, res_date = S["peak_residual"]
longest_sp = S["surplus_periods"][0] if S["surplus_periods"] else None
longest_df = S["deficit_periods"][0] if S["deficit_periods"] else None
st.metric("Längste Superpower / Tage mit Superpower / Ø-Leistung am Tag mit größtem Überschuss",
          f"{fmt_period(longest_sp)} / {S['surplus_days']} Tage / {gn(sp_gw)} GW "
          f"({sp_date:%d.%m.%Y})" if sp_date is not None else f"{fmt_period(longest_sp)} / 0 Tage / –")
st.metric("Längste Dunkelflaute / Tage mit EE-Defizit / Ø-Residualleistung am Tag mit größtem Defizit",
          f"{fmt_period(longest_df)} / {S['deficit_days']} Tage / {gn(res_gw)} GW "
          f"({res_date:%d.%m.%Y})" if res_date is not None else f"{fmt_period(longest_df)} / 0 Tage / –")

if S["deficit_periods"]:
    st.subheader("Top-10 Dunkelflauten")
    top = pd.DataFrame([{
        "Nr": i, "Start": f"{p.start:%d.%m.%Y}", "Ende": f"{p.end:%d.%m.%Y}", "Dauer [Tage]": p.days,
        "Gesamtdefizit [TWh]": gn(p.energy_twh), "Jahresnaht": "↻" if p.wraps else "",
    } for i, p in enumerate(S["deficit_periods"][:10], 1)])
    st.dataframe(top, width="stretch", hide_index=True, column_config={
        "Nr": st.column_config.NumberColumn(width="small"),
        "Jahresnaht": st.column_config.TextColumn(width="small",
                                                  help="↻ = läuft über die Zeitraumgrenze (stationäres Jahr)"),
    })

st.caption(texts.DUNKELFLAUTE_DEFINITION)

c1, c2, c3 = st.columns(3)
c1.metric("EE-Anteil am Verbrauch", gn(S["ee_share"] * 100) + " %")
c2.metric(f"Ø Temperatur ({location})", gn(S["avg_temp"]) + " °C" if S["avg_temp"] is not None else "n/a")
c3.metric("Heizgradtage (15 °C)", gn(S["hgt"], 0) if S["hgt"] is not None else "n/a",
          help=f"Referenz (Normaljahr {config.HGT_REFERENCE_YEARS[0]}–{config.HGT_REFERENCE_YEARS[1]}): "
               + (gn(hgt_ref, 0) if hgt_ref else "n/a"))

# ---------------------------------------------------------------------------
# Erläuterungen, Debug, Annahmen, Spenden
# ---------------------------------------------------------------------------
with st.expander("Erläuterung zur Darstellung und Annahmen", expanded=True):
    st.markdown(texts.EXPLANATION)

with st.expander("Debug: Datenbasis und tägliche Differenz EE – Verbrauch", expanded=False):
    st.markdown(texts.DEBUG_COLUMNS)
    st.markdown("**Datenquellen und Abrufzeitpunkte**")
    st.markdown("\n".join(f"- {x}" for x in sources) or "–")
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
                  "superpower_day", "residual_day", "other_ee", "pumped_storage_net",
                  *[f"installed_{k}" for k in model.GEN_KEYS], *[f"scale_{k}" for k in model.GEN_KEYS]]
    debug_df = df[debug_cols].copy()
    debug_df["date"] = debug_df["date"].dt.strftime("%d.%m.%Y")
    st.dataframe(debug_df, width="stretch", hide_index=True)
    st.download_button("Alle Tageswerte als CSV", df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
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
