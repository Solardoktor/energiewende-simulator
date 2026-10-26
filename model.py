"""Rechenmodell: skalierte Erzeugung, Sektorlasten, Tagesbilanz, Kennzahlen.

Alle Funktionen sind rein (keine Streamlit-Aufrufe, keine Netzzugriffe) und
dadurch testbar und cachebar.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config

GEN_KEYS = ("solar", "wind_onshore", "wind_offshore")
GEN_SERIES = {"solar": config.SOLAR_SERIES, "wind_onshore": config.WIND_ONSHORE_SERIES,
              "wind_offshore": config.WIND_OFFSHORE_SERIES}
LOAD_COLS = ("base_load_gwh", "mobility_gwh", "dhw_gwh", "heating_gwh")


@dataclass(frozen=True)
class Params:
    scaling_enabled: bool = True
    target_solar: float = 130.0
    target_wind_onshore: float = 70.0
    target_wind_offshore: float = 12.0
    include_pumped_storage: bool = False
    load_scale: float = 1.0
    add_ev_mio: float = 0.0
    add_lkw_mio: float = 0.0
    weekend_share: float = 0.2
    add_dhw_mio: float = 0.0
    add_heating_mio: float = 0.0
    share_air_hp: float = 0.6
    share_water_hp: float = 0.1
    co2_specific: float = config.CO2_DEFAULT_G_PER_KWH
    hgt_reference: float | None = None

    @property
    def share_direct(self) -> float:
        return max(0.0, 1.0 - self.share_air_hp - self.share_water_hp)

    def target(self, key: str) -> float:
        return {"solar": self.target_solar, "wind_onshore": self.target_wind_onshore,
                "wind_offshore": self.target_wind_offshore}[key]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def cop_air(temp) -> np.ndarray:
    c = config.COP_AIR
    t = np.nan_to_num(np.asarray(temp, dtype=float), nan=config.COP_DEFAULT_TEMP)
    return np.clip(c["a"] + c["b"] * t, c["lo"], c["hi"])


def cop_water(temp) -> np.ndarray:
    c = config.COP_WATER
    t = np.nan_to_num(np.asarray(temp, dtype=float), nan=config.COP_DEFAULT_TEMP)
    return np.clip(c["a"] + c["b"] * t, c["lo"], c["hi"])


def ev_consumption_factor(temp) -> np.ndarray:
    """Relativer Verbrauch je km in Abhängigkeit von der Tagesmitteltemperatur."""
    t = np.asarray(temp, dtype=float)
    return (1.0 + config.EV_COLD_PER_K * np.maximum(0, config.EV_COLD_BELOW - t)
            + config.EV_HOT_PER_K * np.maximum(0, t - config.EV_HOT_ABOVE))


def distribute(total: float, weights) -> np.ndarray:
    """Verteilt ``total`` proportional zu ``weights`` (Summe bleibt exakt erhalten)."""
    w = np.asarray(weights, dtype=float)
    s = w.sum()
    return np.zeros_like(w) if s <= 0 or total == 0 else total * w / s


def mobility_profile(total: float, weights, weekend_or_holiday, weekend_share: float) -> np.ndarray:
    """Anteil ``weekend_share`` der Ladeenergie wird gezielt auf Wochenende/Feiertage gelegt,
    der Rest folgt dem saisonalen Profil über alle Tage. Summe = ``total``."""
    w = np.asarray(weights, dtype=float)
    we = np.asarray(weekend_or_holiday, dtype=bool)
    s = float(np.clip(weekend_share, 0, 1)) if we.any() else 0.0
    return distribute(total * (1 - s), w) + distribute(total * s, w * we)


# ---------------------------------------------------------------------------
# Hauptberechnung
# ---------------------------------------------------------------------------
def compute_daily(raw: pd.DataFrame, installed_daily: pd.DataFrame, temps: pd.DataFrame,
                  holiday_dates: frozenset, p: Params) -> pd.DataFrame:
    """Tagesbilanz für den Zeitraum in ``raw`` (eine Zeile je lokalem Kalendertag)."""
    if config.LOAD_SERIES not in raw.columns:
        raise KeyError(f"Lastserie '{config.LOAD_SERIES}' fehlt in den Energy-Charts-Daten.")
    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    n = len(df)
    period_frac = n / 365.0
    month = df["date"].dt.month

    # Temperatur (Lücken interpolieren)
    if temps is not None and not temps.empty and temps["temp_mean"].notna().any():
        t = temps.set_index(pd.to_datetime(temps["date"]))["temp_mean"]
        t = t[~t.index.duplicated(keep="last")]
        df["temp_mean"] = t.reindex(df["date"]).interpolate(limit_direction="both").to_numpy()
        has_temp = True
    else:
        df["temp_mean"] = np.nan
        has_temp = False

    # Basis-Last
    df["base_load_gwh"] = df[config.LOAD_SERIES].fillna(0.0) * p.load_scale

    # Mobilität: Fahrleistung (Monat) × Verbrauch je km (Temperatur) × Wochenend-Verschiebung
    annual_mob = (p.add_ev_mio * config.KWH_PER_EV_PER_YEAR + p.add_lkw_mio * config.KWH_PER_LKW_PER_YEAR)  # GWh
    drive = month.map(config.DRIVING_MONTH_FACTORS).to_numpy(dtype=float)
    cons = (ev_consumption_factor(df["temp_mean"]) if has_temp
            else month.map(config.EV_CONSUMPTION_MONTH_FALLBACK).to_numpy(dtype=float))
    df["is_weekend_or_holiday"] = (df["date"].dt.weekday >= 5) | df["date"].dt.date.isin(holiday_dates)
    df["mobility_gwh"] = mobility_profile(annual_mob * period_frac, drive * cons,
                                          df["is_weekend_or_holiday"], p.weekend_share)

    # Warmwasser
    annual_dhw = p.add_dhw_mio * config.KWH_PER_PERSON_DHW_YEAR          # GWh
    f_dhw = np.where(month.isin(config.DHW_WINTER_MONTHS), config.DHW_WINTER_FACTOR, config.DHW_SUMMER_FACTOR)
    df["dhw_gwh"] = distribute(annual_dhw * period_frac, f_dhw)

    # Heizung: Wärmebedarf ∝ Heizgradtagen, kalibriert auf ein Normaljahr
    annual_heat = p.add_heating_mio * config.HEAT_KWH_PER_PERSON_YEAR    # GWh Wärme
    if has_temp:
        hgt = np.maximum(0.0, config.HGT_BASE_TEMP - df["temp_mean"].to_numpy())
        ref = p.hgt_reference if p.hgt_reference else hgt.sum() / period_frac
        df["heat_demand_gwh"] = annual_heat * hgt / ref if ref > 0 else 0.0
    else:
        f_heat = month.map(config.HEATING_MONTH_FALLBACK).to_numpy(dtype=float)
        df["heat_demand_gwh"] = distribute(annual_heat * period_frac, f_heat)
    df["cop_air"] = cop_air(df["temp_mean"])
    df["cop_water"] = cop_water(df["temp_mean"])
    df["heating_gwh"] = df["heat_demand_gwh"] * (p.share_direct + p.share_air_hp / df["cop_air"]
                                                 + p.share_water_hp / df["cop_water"])

    df["total_load_gwh"] = df[list(LOAD_COLS)].sum(axis=1)

    # Erzeugung Solar/Wind, optional auf Zielleistung skaliert
    inst = installed_daily.reindex(df["date"])
    for k in GEN_KEYS:
        gen = df[GEN_SERIES[k]].fillna(0.0) if GEN_SERIES[k] in df else pd.Series(0.0, index=df.index)
        df[f"installed_{k}"] = inst[k].to_numpy()
        if p.scaling_enabled:
            factor = (p.target(k) / inst[k].replace(0, np.nan)).fillna(0.0).to_numpy()
        else:
            factor = np.ones(n)
        df[f"scale_{k}"] = factor
        df[f"{k}_scaled"] = gen.to_numpy() * factor

    # Sonstige EE (Whitelist, unskaliert) und optional Pumpspeicher (netto)
    other = pd.Series(0.0, index=df.index)
    for col, weight in config.OTHER_EE_SERIES.items():
        if col in df:
            other += df[col].fillna(0.0) * weight
    df["pumped_storage_net"] = sum((df[c].fillna(0.0) for c in config.PUMPED_STORAGE_SERIES if c in df),
                                   pd.Series(0.0, index=df.index))
    if p.include_pumped_storage:
        other += df["pumped_storage_net"]
    df["other_ee"] = other

    # Bilanz
    df["ee_day"] = df[[f"{k}_scaled" for k in GEN_KEYS]].sum(axis=1) + df["other_ee"]
    df["ee_minus_load"] = df["ee_day"] - df["total_load_gwh"]
    df["superpower_day"] = np.maximum(0.0, df["ee_minus_load"])
    df["residual_day"] = np.maximum(0.0, -df["ee_minus_load"])
    df["residual_share"] = np.where(df["total_load_gwh"] > 0,
                                    df["residual_day"] / df["total_load_gwh"].replace(0, np.nan), 0.0)
    return df


# ---------------------------------------------------------------------------
# Zusammenhängende Zeiträume (Dunkelflauten, Superpower) – stationäres Jahr
# ---------------------------------------------------------------------------
def find_runs(mask, cyclic: bool = True) -> list[tuple[int, int]]:
    """Zusammenhängende True-Abschnitte als (Startindex, Länge).

    ``cyclic=True`` behandelt den Zeitraum als stationäres Jahr: Ende und Anfang
    werden verknüpft, ein Abschnitt kann über die Zeitraumgrenze laufen
    (Startindex nahe Ende, Länge reicht über den Anfang hinaus).
    """
    m = np.asarray(mask, dtype=bool)
    n = len(m)
    if n == 0 or not m.any():
        return []
    if m.all():
        return [(0, n)]
    shift = int(np.argmin(m)) if cyclic else 0          # Rotation, sodass Index 0 = False
    r = np.roll(m, -shift)
    d = np.diff(np.concatenate(([0], r.astype(int), [0])))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    return [((s + shift) % n, int(e - s)) for s, e in zip(starts, ends)]


@dataclass(frozen=True)
class Period:
    start: pd.Timestamp
    end: pd.Timestamp
    days: int
    energy_twh: float
    wraps: bool              # läuft über die Zeitraumgrenze (Jahresnaht)


def periods(dates: pd.Series, mask, energy_gwh, cyclic: bool = True) -> list[Period]:
    """Alle Zeiträume, sortiert nach Dauer (absteigend), bei Gleichstand nach Energie."""
    dates = pd.to_datetime(pd.Series(dates)).reset_index(drop=True)
    e = np.asarray(energy_gwh, dtype=float)
    n = len(dates)
    out = []
    for s, length in find_runs(mask, cyclic):
        idx = (s + np.arange(length)) % n
        out.append(Period(dates.iloc[idx[0]], dates.iloc[idx[-1]], length,
                          float(e[idx].sum()) / 1000.0, bool(idx[-1] < idx[0])))
    return sorted(out, key=lambda x: (-x.days, -x.energy_twh))


# ---------------------------------------------------------------------------
# Jahresbilanz / Kennzahlen
# ---------------------------------------------------------------------------
def summarize(df: pd.DataFrame, p: Params, cyclic: bool = True) -> dict:
    sums = {c: float(df[c].sum()) for c in
            ["solar_scaled", "wind_onshore_scaled", "wind_offshore_scaled", "other_ee", "ee_day",
             *LOAD_COLS, "total_load_gwh", "residual_day", "superpower_day"]}
    cons = sums["total_load_gwh"]
    residual = sums["residual_day"]

    # EE-Anteil = gedeckter Verbrauch / Verbrauch = 1 − Residual/Verbrauch
    ee_share = 1.0 - residual / cons if cons > 0 else 0.0
    co2_mean = residual / cons * p.co2_specific if cons > 0 else 0.0

    def co2_of(col):  # tagesgewichtet nach Verbrauch der Kategorie
        w = df[col].sum()
        return float((df[col] * df["residual_share"]).sum() / w * p.co2_specific) if w > 0 else 0.0

    deficit = periods(df["date"], df["residual_day"] > 0, df["residual_day"], cyclic)
    surplus = periods(df["date"], df["superpower_day"] > 0, df["superpower_day"], cyclic)

    def peak(col):
        if df[col].max() <= 0:
            return 0.0, None
        i = df[col].idxmax()
        return float(df.loc[i, col] / 24.0), df.loc[i, "date"]

    return {
        "sums_gwh": sums,
        "ee_share": ee_share,
        "co2_mean": co2_mean,
        "co2_by_category": {c: co2_of(c) for c in LOAD_COLS},
        "deficit_periods": deficit,
        "surplus_periods": surplus,
        "deficit_days": int((df["residual_day"] > 0).sum()),
        "surplus_days": int((df["superpower_day"] > 0).sum()),
        "peak_residual": peak("residual_day"),     # (Ø GW am Tag mit größtem Defizit, Datum)
        "peak_superpower": peak("superpower_day"),
        "hgt": float(np.maximum(0, config.HGT_BASE_TEMP - df["temp_mean"]).sum())
               if df["temp_mean"].notna().any() else None,
        "avg_temp": float(df["temp_mean"].mean()) if df["temp_mean"].notna().any() else None,
    }


def german_number(n, decimals: int = 1) -> str:
    if n is None or (isinstance(n, float) and np.isnan(n)):
        return "–"
    return f"{n:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
