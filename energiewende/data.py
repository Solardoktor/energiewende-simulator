"""Abruf und Aufbereitung der Eingangsdaten (Energy-Charts, Open-Meteo).

Dieses Modul ist frei von Streamlit-Aufrufen, damit es in Tests und im
Aktualisierungsskript verwendet werden kann. Hinweise für die Oberfläche werden
als Liste von Texten zurückgegeben.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config
from .store import Entry, Store, max_age_days, needs_refresh, year_needed_until

COVERAGE_OK = 0.99
_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
log = logging.getLogger("energiewende")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [energiewende] %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

# Fail fast: höchstens ein kurzer Wiederholversuch bei Serverfehlern, kein Warten bei
# Rate-Limit (429) – sonst „hängt“ die Seite minutenlang. Lange Lesezeit nur für
# große Jahresabrufe.
TIMEOUT_DEFAULT = (10, 30)
TIMEOUT_LARGE = (10, 120)


class RateLimited(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=1, backoff_factor=1, status_forcelist=(500, 502, 503, 504),
                  allowed_methods=("GET",), respect_retry_after_header=False, raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = config.HTTP_USER_AGENT
    return s


def fetch_json(url: str, params: dict, timeout: tuple = TIMEOUT_DEFAULT) -> dict:
    """GET mit Protokoll (Dauer, Status, Größe). Wirft bei Fehlern eine Exception."""
    t0 = time.perf_counter()
    log.info("GET %s %s …", url, params)
    try:
        with _session() as s:
            r = s.get(url, params=params, timeout=timeout)
    except requests.Timeout as e:
        log.warning("TIMEOUT nach %.1f s: %s", time.perf_counter() - t0, url)
        raise TimeoutError(f"keine Antwort innerhalb von {timeout[1]} s") from e
    dt = time.perf_counter() - t0
    log.info("→ HTTP %s in %.1f s, %.1f MB", r.status_code, dt, len(r.content) / 1e6)
    if r.status_code == 429:
        raise RateLimited(f"Rate-Limit erreicht (HTTP 429, Retry-After {r.headers.get('Retry-After', '?')} s)")
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _records(js) -> list:
    if not isinstance(js, dict):
        return []
    recs = js.get("data")
    return recs if isinstance(recs, list) else []


def to_local_index(raw) -> pd.DatetimeIndex:
    """Zeitstempel → tz-aware Index in Europe/Berlin.

    ISO-Strings mit Offset werden umgerechnet, solche ohne Offset als Ortszeit
    interpretiert, Zahlen als Unix-Sekunden.
    """
    raw = list(raw)
    if raw and isinstance(raw[0], (int, float, np.integer, np.floating)):
        return pd.DatetimeIndex(pd.to_datetime(raw, unit="s", utc=True)).tz_convert(config.TIMEZONE)
    strs = [str(x) for x in raw]
    if strs and _OFFSET_RE.search(strs[0]):
        return pd.DatetimeIndex(pd.to_datetime(strs, utc=True, errors="coerce")).tz_convert(config.TIMEZONE)
    naive = pd.DatetimeIndex(pd.to_datetime(strs, errors="coerce"))
    try:
        return naive.tz_localize(config.TIMEZONE, ambiguous="infer", nonexistent="shift_forward")
    except Exception:
        return naive.tz_localize(config.TIMEZONE, ambiguous="NaT", nonexistent="shift_forward")


def _unit_factor_to_gw(js) -> float:
    unit = str((js or {}).get("unit") or "").strip().upper()
    return 1.0 if unit.startswith("GW") else 1 / 1000.0   # Default: MW


def day_length_hours(dates: pd.DatetimeIndex) -> np.ndarray:
    """Länge der lokalen Kalendertage in Stunden (23/24/25 bei Zeitumstellung)."""
    d0 = pd.DatetimeIndex(dates).tz_localize(config.TIMEZONE)
    d1 = (pd.DatetimeIndex(dates) + pd.Timedelta(days=1)).tz_localize(config.TIMEZONE)
    return ((d1 - d0) / pd.Timedelta(hours=1)).to_numpy(dtype=float)


def power_to_daily_energy(js) -> pd.DataFrame:
    """Leistungs-Zeitreihe (v2-Envelope) → Tagesenergie in GWh je lokalem Kalendertag.

    * Tagesgrenzen in deutscher Ortszeit (nicht UTC)
    * Intervalllänge je Datensatz aus den Zeitstempeln (robust bei 15-min/60-min-Wechsel)
    * Spalte ``coverage`` = Anteil der Stunden des Tages mit Daten (1,0 = vollständig)
    """
    recs = _records(js)
    if not recs:
        return pd.DataFrame()
    idx = to_local_index([r.get("timestamp") for r in recs])
    vals = pd.DataFrame([r.get("values") or {} for r in recs], index=idx)
    vals = vals.apply(pd.to_numeric, errors="coerce")
    vals = vals[vals.index.notna()]
    vals = vals[~vals.index.duplicated(keep="last")].sort_index()
    if vals.empty:
        return pd.DataFrame()

    # Intervall je Datensatz: Abstand zum nächsten Zeitstempel; Lücken (> 1 h) → letzter regulärer Schritt
    t = vals.index
    if len(t) > 1:
        diffs = (t[1:] - t[:-1]) / pd.Timedelta(hours=1)
        step = pd.Series(np.append(diffs, diffs[-1]), index=t)
    else:
        step = pd.Series([(js.get("interval_minutes") or 15) / 60], index=t)
    step = step.where(step <= 1.0).ffill().bfill().fillna(0.25)

    energy = vals.mul(step, axis=0) * _unit_factor_to_gw(js)      # GW × h = GWh
    day = pd.DatetimeIndex(t.tz_localize(None)).normalize()
    daily = energy.groupby(day).sum(min_count=1)
    hours = step.where(vals.notna().any(axis=1), 0.0).groupby(day).sum()
    daily["coverage"] = np.clip(hours.to_numpy() / day_length_hours(daily.index), 0, 1)
    daily.index.name = "date"
    return daily.reset_index()


def parse_installed(js) -> pd.DataFrame:
    """Monatliche installierte Leistung (GW) mit Stichtag Monatsende."""
    recs = _records(js)
    if not recs:
        return pd.DataFrame()
    idx = to_local_index([r.get("timestamp") for r in recs])
    raw = pd.DataFrame([r.get("values") or {} for r in recs], index=idx).apply(pd.to_numeric, errors="coerce")
    unit = str(js.get("unit") or "GW").strip().upper()
    if unit.startswith("MW"):
        raw = raw / 1000.0
    out = pd.DataFrame(index=raw.index)
    for target, candidates in config.INSTALLED_SERIES_CANDIDATES.items():
        col = next((c for c in candidates if c in raw.columns), None)
        out[target] = raw[col] if col else np.nan
    months = pd.DatetimeIndex(raw.index.tz_localize(None)).to_period("M")
    out["month_end"] = months.to_timestamp(how="end").normalize()
    return out.reset_index(drop=True).dropna(subset=["solar", "wind_onshore", "wind_offshore"], how="all")


def parse_temperatures(js) -> pd.DataFrame:
    try:
        d = js["daily"]
        df = pd.DataFrame({"date": pd.to_datetime(d["time"]),
                           "temp_mean": pd.to_numeric(pd.Series(d["temperature_2m_mean"]), errors="coerce")})
        return df
    except Exception:
        return pd.DataFrame()


def daily_installed(installed: pd.DataFrame, dates: pd.Series) -> tuple[pd.DataFrame, bool]:
    """Installierte Leistung je Tag, linear zwischen den Monatsend-Stichtagen interpoliert.

    Rückgabe: (DataFrame mit Spalten solar/wind_onshore/wind_offshore, Fallback verwendet?)
    """
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    cols = list(config.FALLBACK_INSTALLED_GW)
    if installed is None or installed.empty:
        return pd.DataFrame({c: config.FALLBACK_INSTALLED_GW[c] for c in cols}, index=dates), True
    s = installed.set_index("month_end")[cols].sort_index()
    s = s[~s.index.duplicated(keep="last")]
    full = s.reindex(s.index.union(dates)).interpolate(method="time", limit_area="inside").ffill().bfill()
    out = full.reindex(dates)
    for c in cols:
        if out[c].isna().all():
            out[c] = config.FALLBACK_INSTALLED_GW[c]
    return out, False


# ---------------------------------------------------------------------------
# Laden mit Ablage und Aktualisierungsregeln
# ---------------------------------------------------------------------------
@dataclass
class LoadResult:
    df: pd.DataFrame
    notes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


def _complete_until(daily: pd.DataFrame, first: date) -> date | None:
    """Letzter Tag, bis zu dem alle Tage ab ``first`` vollständig vorliegen."""
    if daily.empty:
        return None
    s = daily.set_index("date")["coverage"]
    expected = pd.date_range(first, s.index.max(), freq="D")
    ok = s.reindex(expected).fillna(0) >= COVERAGE_OK
    if not ok.iloc[0]:
        return None
    bad = np.where(~ok.to_numpy())[0]
    last = expected[bad[0] - 1] if len(bad) else expected[-1]
    return last.date()


def fetch_power_year(year: int, needed_until: date) -> pd.DataFrame:
    """Lädt ein (Teil-)Jahr; fragt je einen Tag Puffer an und schneidet lokal zu."""
    params = {"country": "de",
              "start": (date(year, 1, 1) - timedelta(days=1)).isoformat(),
              "end": (needed_until + timedelta(days=1)).isoformat()}
    js = fetch_json(f"{config.ENERGY_CHARTS_URL}/total_power", params, TIMEOUT_LARGE)
    t0 = time.perf_counter()
    daily = power_to_daily_energy(js)
    log.info("total_power %s: %d Datensätze → %d Tage (inkl. Puffertage), %d Serien, Aufbereitung %.1f s", year,
             len(_records(js)), len(daily), max(0, daily.shape[1] - 2), time.perf_counter() - t0)
    if daily.empty:
        return daily
    mask = (daily["date"] >= pd.Timestamp(year, 1, 1)) & (daily["date"] <= pd.Timestamp(needed_until))
    return daily.loc[mask].reset_index(drop=True)


def load_power_year(store: Store, year: int, needed_until: date, today: date,
                    force: bool = False) -> tuple[Entry | None, list[str]]:
    notes: list[str] = []
    entry = store.load(f"power_{year}")
    if force or needs_refresh(entry, needed_until, max_age_days(year, today)):
        try:
            daily = fetch_power_year(year, needed_until)
            if daily.empty:
                raise ValueError("leere Antwort")
            cu = _complete_until(daily, date(year, 1, 1))
            if (cu is None or cu < needed_until) and (today - needed_until).days > 45:
                # Lücken in länger zurückliegenden Daten sind dauerhaft (Quelle) – nicht ständig neu abrufen
                cu = needed_until
            if entry is None or (cu or date.min) >= (entry.complete_until or date.min):
                entry = store.save(f"power_{year}", daily, cu)
        except Exception as e:  # noqa: BLE001 – API-Fehler → vorhandene Daten weiterverwenden
            if entry is None:
                notes.append(f"Energy-Charts {year}: Abruf fehlgeschlagen ({e}).")
            else:
                notes.append(f"Energy-Charts {year}: Aktualisierung fehlgeschlagen ({e}); "
                             f"verwende Stand vom {entry.fetched_at:%d.%m.%Y}.")
    return entry, notes


def load_power_period(start: date, end: date, today: date, store: Store | None = None) -> LoadResult:
    store = store or Store()
    parts, res = [], LoadResult(pd.DataFrame())
    for year in range(start.year, end.year + 1):
        entry, notes = load_power_year(store, year, year_needed_until(year, end), today)
        res.notes += notes
        if entry is not None:
            parts.append(entry.df)
            res.sources.append(f"Erzeugung/Last {year}: {entry.source}, Abruf {entry.fetched_at:%d.%m.%Y %H:%M} UTC")
    if not parts:
        return res
    df = pd.concat(parts, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
    df = df.drop_duplicates("date", keep="last").sort_values("date")
    expected = pd.date_range(start, end, freq="D")
    df = df.set_index("date").reindex(expected)
    df["coverage"] = df["coverage"].fillna(0.0)
    df.index.name = "date"
    res.df = df.reset_index()
    bad = res.df.loc[res.df["coverage"] < COVERAGE_OK, "date"]
    if len(bad):
        res.notes.append(f"{len(bad)} Tag(e) mit unvollständigen Energy-Charts-Daten "
                         f"(z. B. {bad.iloc[0]:%d.%m.%Y}); Details im Debug-Bereich.")
    return res


def load_installed(today: date, store: Store | None = None, force: bool = False) -> LoadResult:
    store = store or Store()
    res = LoadResult(pd.DataFrame())
    entry = store.load("installed_monthly")
    if force or needs_refresh(entry, date(1900, 1, 1), config.MAX_AGE_INSTALLED_DAYS):
        try:
            js = fetch_json(f"{config.ENERGY_CHARTS_URL}/installed_power",
                            {"country": "de", "time_step": "monthly"})
            inst = parse_installed(js)
            if inst.empty:
                raise ValueError("leere Antwort")
            entry = store.save("installed_monthly", inst, inst["month_end"].max().date())
        except Exception as e:  # noqa: BLE001
            res.notes.append(f"Installierte Leistung: Abruf fehlgeschlagen ({e})"
                             + ("; verwende gespeicherten Stand." if entry else "; verwende Ersatzwerte."))
    if entry is not None:
        res.df = entry.df
        res.sources.append(f"Installierte Leistung: {entry.source}, Abruf {entry.fetched_at:%d.%m.%Y %H:%M} UTC")
    return res


def _slug(name: str) -> str:
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        name = name.lower().replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def _fetch_temps(lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    js = fetch_json(config.OPEN_METEO_ARCHIVE_URL, {
        "latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "temperature_2m_mean", "timezone": config.TIMEZONE}, timeout=TIMEOUT_LARGE)
    return parse_temperatures(js)


def load_temperatures(location: str, start: date, end: date, today: date,
                      store: Store | None = None) -> LoadResult:
    store = store or Store()
    lat, lon = config.LOCATIONS[location]
    res, parts = LoadResult(pd.DataFrame()), []
    for year in range(start.year, end.year + 1):
        key, needed = f"temp_{_slug(location)}_{year}", year_needed_until(year, end)
        entry = store.load(key)
        max_age = 3650 if (entry and entry.complete_until == date(year, 12, 31)) else 30
        if needs_refresh(entry, needed, max_age):
            try:
                t = _fetch_temps(lat, lon, date(year, 1, 1), needed)
                if t.empty:
                    raise ValueError("leere Antwort")
                valid = t.dropna(subset=["temp_mean"])
                cu = valid["date"].max().date() if not valid.empty else None
                entry = store.save(key, t, cu)
            except Exception as e:  # noqa: BLE001
                res.notes.append(f"Temperatur {location} {year}: Abruf fehlgeschlagen ({e}).")
        if entry is not None:
            parts.append(entry.df)
            res.sources.append(f"Temperatur {location} {year}: {entry.source}, Abruf {entry.fetched_at:%d.%m.%Y} UTC")
    if parts:
        df = pd.concat(parts, ignore_index=True)
        df["date"] = pd.to_datetime(df["date"])
        res.df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))].reset_index(drop=True)
    return res


def load_hgt_reference(location: str, store: Store | None = None) -> tuple[float | None, list[str]]:
    """Mittlere jährliche Heizgradtage (Basis 15 °C) der Klimanormalperiode am Standort."""
    store = store or Store()
    y0, y1 = config.HGT_REFERENCE_YEARS
    key = f"hgtref_{_slug(location)}_{y0}_{y1}"
    entry = store.load(key)
    if entry is None:
        try:
            lat, lon = config.LOCATIONS[location]
            t = _fetch_temps(lat, lon, date(y0, 1, 1), date(y1, 12, 31))
            if t.empty:
                raise ValueError("leere Antwort")
            entry = store.save(key, t, date(y1, 12, 31))
        except Exception as e:  # noqa: BLE001
            return None, [f"Referenz-Heizgradtage {location}: Abruf fehlgeschlagen ({e}); "
                          "Heizlast wird auf den gewählten Zeitraum normiert."]
    t = entry.df.dropna(subset=["temp_mean"])
    if t.empty:
        return None, []
    years = pd.to_datetime(t["date"]).dt.year.nunique()
    return float(np.maximum(0, config.HGT_BASE_TEMP - t["temp_mean"]).sum() / years), []


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
