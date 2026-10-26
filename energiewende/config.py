"""Konstanten, Annahmen und Szenarien des Energiewende-Simulators.

Alle fachlichen Annahmen stehen hier zentral, damit Modell, UI und Dokumentation
dieselben Werte verwenden.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Datenquellen
# ---------------------------------------------------------------------------
ENERGY_CHARTS_URL = "https://api.energy-charts.info/v2"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HTTP_USER_AGENT = "SDR-Energiewende-Simulator (+https://www.solardoktor.de)"
TIMEZONE = "Europe/Berlin"
FIRST_YEAR = 2015

ROOT_DIR = Path(__file__).resolve().parent.parent
BUNDLED_DATA_DIR = ROOT_DIR / "data"      # im Repo versioniert (per GitHub Action aktualisiert)
RUNTIME_CACHE_DIR = ROOT_DIR / "cache"    # zur Laufzeit beschreibbar, nicht versioniert

# Aktualisierungsregeln (Tage) – Energy-Charts korrigiert Werte gelegentlich rückwirkend
MAX_AGE_OLD_YEARS_DAYS = 365      # Jahre vor dem letzten vollständigen Kalenderjahr: jährlich
MAX_AGE_RECENT_DAYS = 30          # letztes vollständiges Jahr und laufendes Jahr: monatlich
MAX_AGE_INSTALLED_DAYS = 7        # installierte Leistung: wöchentlich
MIN_RETRY_HOURS = 6               # frühestens erneut versuchen, wenn Daten noch unvollständig sind

# ---------------------------------------------------------------------------
# Serien der Energy-Charts-API (v2, snake_case-IDs)
# ---------------------------------------------------------------------------
LOAD_SERIES = "load_incl_self_consumption"
SOLAR_SERIES = "solar"
WIND_ONSHORE_SERIES = "wind_onshore"
WIND_OFFSHORE_SERIES = "wind_offshore"

# Sonstige erneuerbare Erzeugung (unskaliert) mit Anrechnungsfaktor
OTHER_EE_SERIES = {
    "biomass": 1.0,
    "hydro_run_of_river": 1.0,
    "hydro_water_reservoir": 1.0,
    "geothermal": 1.0,
    "waste": 0.5,   # Konvention: 50 % biogener Anteil des Abfalls
}
# Pumpspeicher (Erzeugung positiv, Ladung negativ; Summe ≈ Speicherverluste)
PUMPED_STORAGE_SERIES = ["hydro_pumped_storage", "hydro_pumped_storage_consumption"]

# Installierte Leistung (v2/installed_power, monatlich, Wert gilt zum Monatsende)
INSTALLED_SERIES_CANDIDATES = {
    "solar": ["solar_dc", "solar"],
    "wind_onshore": ["wind_onshore"],
    "wind_offshore": ["wind_offshore"],
}
FALLBACK_INSTALLED_GW = {"solar": 130.0, "wind_onshore": 70.0, "wind_offshore": 12.0}

# ---------------------------------------------------------------------------
# Szenarien (GWp / GW). "Status Quo" wird aus den Daten abgeleitet.
# ---------------------------------------------------------------------------
STATUS_QUO = "Status Quo (Ist-Leistung am Zeitraumende)"
SCENARIOS = {
    STATUS_QUO: None,
    "Ziele der Bundesregierung für 2030": {"solar": 215.0, "wind_onshore": 115.0, "wind_offshore": 30.0},
    "Agora Energiewende: Klimaneutrales Deutschland 2045": {"solar": 400.0, "wind_onshore": 180.0, "wind_offshore": 70.0},
}

# ---------------------------------------------------------------------------
# Temperatur-Standorte (München ist Default und wird standardmäßig geladen)
# ---------------------------------------------------------------------------
DEFAULT_LOCATION = "München"
LOCATIONS = {
    "München": (48.1351, 11.5820),
    "Berlin": (52.5200, 13.4050),
    "Hamburg": (53.5511, 9.9937),
    "Hannover": (52.3759, 9.7320),
    "Köln": (50.9375, 6.9603),
    "Frankfurt am Main": (50.1109, 8.6821),
    "Stuttgart": (48.7758, 9.1829),
    "Nürnberg": (49.4521, 11.0767),
    "Leipzig": (51.3397, 12.3731),
    "Dresden": (51.0504, 13.7373),
    "Freiburg im Breisgau": (47.9990, 7.8421),
}
# Referenzperiode für Heizgradtage (Klimanormalperiode)
HGT_REFERENCE_YEARS = (1991, 2020)
HGT_BASE_TEMP = 15.0   # Heizgrenztemperatur °C

# ---------------------------------------------------------------------------
# Sektorenkopplung – Annahmen
# ---------------------------------------------------------------------------
KWH_PER_100KM_PKW = 18.0          # inkl. Ladeverluste, Jahresmittel
ANNUAL_KM_PKW = 12_500
KWH_PER_EV_PER_YEAR = ANNUAL_KM_PKW * KWH_PER_100KM_PKW / 100      # 2 250 kWh/a

KWH_PER_100KM_LKW = 50.0
ANNUAL_KM_LKW = 80_000
KWH_PER_LKW_PER_YEAR = ANNUAL_KM_LKW * KWH_PER_100KM_LKW / 100     # 40 000 kWh/a

KWH_PER_PERSON_DHW_YEAR = 2_000
HEAT_KWH_PER_PERSON_YEAR = 10_000  # Wärmebedarf (= Strom bei Direktheizung) im Normaljahr

CURRENT_EV = 2        # Mio. (Bestand, gerundet)
CURRENT_LKW = 0.0
CURRENT_DHW = 15      # Mio. EW
CURRENT_HEATING = 5   # Mio. EW

# Mobilität, Fahrleistung je Monat (relativ, Ø ≈ 1): stilisiert, im Sommer höher
# (Urlaubs-/Freizeitverkehr). Größenordnung ±5–6 % orientiert an monatlichen
# Inlandsablieferungen von Ottokraftstoff (BAFA/UFOP); Ablieferungen sind nur ein
# Näherungswert für die Fahrleistung (Lagerhaltung, Preiseffekte).
DRIVING_MONTH_FACTORS = {1: 0.94, 2: 0.95, 3: 0.97, 4: 1.02, 5: 1.03, 6: 1.04,
                         7: 1.06, 8: 1.05, 9: 1.03, 10: 1.00, 11: 0.96, 12: 0.95}
# Mobilität, Verbrauch je km abhängig von der Tagesmitteltemperatur:
# +1,2 %/K unter 15 °C (Heizung, Batterie), +0,5 %/K über 22 °C (Klimatisierung)
EV_COLD_PER_K = 0.012
EV_COLD_BELOW = 15.0
EV_HOT_PER_K = 0.005
EV_HOT_ABOVE = 22.0
# Fallback ohne Temperaturdaten: Verbrauchsfaktor je Monat
EV_CONSUMPTION_MONTH_FALLBACK = {1: 1.17, 2: 1.16, 3: 1.11, 4: 1.06, 5: 1.02, 6: 1.00,
                                 7: 1.00, 8: 1.00, 9: 1.01, 10: 1.06, 11: 1.12, 12: 1.16}

# Warmwasser: im Winter etwas höher (kälteres Kaltwasser)
DHW_WINTER_MONTHS = (11, 12, 1, 2, 3)
DHW_WINTER_FACTOR = 1.10
DHW_SUMMER_FACTOR = 0.95

# Heizung, Fallback ohne Temperaturdaten (relativ; wird normiert)
HEATING_MONTH_FALLBACK = {1: 1.8, 2: 1.6, 3: 1.2, 4: 0.6, 5: 0.2, 6: 0.05,
                          7: 0.0, 8: 0.05, 9: 0.3, 10: 0.8, 11: 1.3, 12: 1.7}

# Wärmepumpen-COP (linear in Tagesmitteltemperatur, begrenzt)
COP_AIR = dict(a=2.2, b=0.08, lo=1.8, hi=4.5)
COP_WATER = dict(a=4.0, b=0.03, lo=3.5, hi=5.5)
COP_DEFAULT_TEMP = 5.0

CO2_DEFAULT_G_PER_KWH = 1000
