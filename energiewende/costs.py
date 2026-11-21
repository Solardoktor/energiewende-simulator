"""Kostenvergleich Direktheizung (Wärmepumpen-Mix) vs. Speicherheizung (Hochtemperatur-Sandspeicher).

Verglichen werden die Speicher-Haushalte (N_S Mio. Einwohner) in zwei Varianten:

* Direktheizung: Zustand Z0 – Wärmepumpen-Mix (Luft-WP, Sole-WP, direktelektrisch) mit Tages-COP,
  Warmwasser wie bisher; Strombezug unflexibel.
* Speicherheizung: Zustand Z2 – direktelektrisch (COP 1) über den Langzeit-Wärmespeicher; Strombezug flexibel.

Je Variante: Investitionen (Wärmepumpen, Residualkraftwerke, Speicher) als Annuität, Energiekosten mit
tagesweisem Preismix (Anteil Residual-/Erdgasstrom wie in der CO₂-Rechnung) sowie Netzentgelte/Umlagen.
Alle Energien in GWh; 1 GWh × 1 ct/kWh = 10 000 €.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config
from .model import Params, cop_air, cop_water
from .storage import StorageResult, heat_per_ew_day


@dataclass(frozen=True)
class CostParams:
    wp_eur_per_kw: float = 8000.0          # Wärmepumpe je kW elektrisch
    residual_eur_per_kw: float = 2000.0    # Residualkraftwerk (Erdgas) je kW
    storage_eur_per_kwh: float = 15.0      # Hochtemperatur-Sandspeicher je kWh Kapazität
    price_gas_ct: float = 18.0             # Strompreis Residual-/Erdgasstrom
    price_surplus_ct: float = 1.0          # Strompreis Überschussstrom (Superpower)
    fee_inflexible_ct: float = 17.0        # Netzentgelte/Umlagen unflexibler Bezug (Direktheizung)
    fee_flexible_ct: float = 10.0          # Netzentgelte/Umlagen flexibler Bezug (Speicherheizung)
    life_wp_years: int = 20                # Nutzungsdauer Wärmepumpen
    life_res_years: int = 40               # Nutzungsdauer Erdgaskraftwerke
    life_sto_years: int = 50               # Nutzungsdauer Langzeit-Wärmespeicher
    interest_pct: float = 3.0


def annuity_factor(interest_pct: float, years: int) -> float:
    i, n = interest_pct / 100.0, max(1, int(years))
    return 1.0 / n if i == 0 else i * (1 + i) ** n / ((1 + i) ** n - 1)


def _energy_cost_mrd(load_gwh: np.ndarray, residual_share: np.ndarray, c: CostParams) -> float:
    """Energiekosten (Mrd. €) mit Tagespreis = Residualanteil × Erdgaspreis + Rest × Überschusspreis."""
    price_ct = residual_share * c.price_gas_ct + (1.0 - residual_share) * c.price_surplus_ct
    return float((load_gwh * price_ct).sum()) / 1e5


def compute_costs(df0: pd.DataFrame, r: StorageResult, p: Params, c: CostParams,
                  peak_res_z0_gw: float, peak_res_z2_gw: float) -> dict | None:
    """Jahreskosten beider Varianten für die Speicher-Haushalte; None, wenn der Speicher nicht aktiv ist.

    peak_res_z0_gw / peak_res_z2_gw: höchste Residualleistung (Ø Tagesleistung) ohne bzw. mit Speicher,
    also die Höchstwerte der geordneten Jahresdauerlinie. Die Differenz ist die Residualleistung, die
    die Variante mit dem höheren Höchstwert zusätzlich benötigt.
    """
    if not r.active:
        return None
    z1, z2, k = r.z1, r.z2, r.kpi
    n_s = k["n_s"]
    h_s = z1["storage_h_s"].to_numpy()
    ww_s = z1["storage_ww_s"].to_numpy()
    ww_ref = ww_s / config.STORAGE_DHW_FACTOR
    mix = (p.share_direct + p.share_air_hp / df0["cop_air"] + p.share_water_hp / df0["cop_water"]).to_numpy()
    out = (z2["storage_out_dhw_gwh"] + z2["storage_out_heat_gwh"]).to_numpy()
    s_in = z2["storage_in_gwh"].to_numpy()

    # Strombezug der Speicher-Haushalte je Tag
    load_a = h_s * mix + ww_ref                       # Direktheizung (Wärmepumpen-Mix)
    load_b = h_s + ww_s - out + s_in                  # Speicherheizung (direkt + Speicherladung)
    rs_a = df0["residual_share"].to_numpy()
    rs_b = z2["residual_share"].to_numpy()

    # Investitionen
    temps_ok = df0["temp_mean"].notna().any()
    if temps_ok and p.hgt_reference:
        ref = p.hgt_reference
    elif temps_ok:
        hgt = np.maximum(0.0, config.HGT_BASE_TEMP - df0["temp_mean"].to_numpy())
        ref = hgt.sum() * 365.0 / len(df0)
    else:
        ref = None
    if ref:
        heat_kw = heat_per_ew_day(p.design_temp, ref) / 24.0                 # kW Wärme je EW bei Auslegung
        wp_kw_el = heat_kw * (p.share_air_hp / float(cop_air([p.design_temp])[0])
                              + p.share_water_hp / float(cop_water([p.design_temp])[0]))
    else:
        heat_kw = wp_kw_el = float("nan")
    wp_gw = n_s * wp_kw_el                                                   # Mio. EW × kW = GW
    delta = peak_res_z2_gw - peak_res_z0_gw
    res_a_gw, res_b_gw = max(0.0, -delta), max(0.0, delta)          # Mehrbedarf der jeweils höheren Variante
    cap_gwh = k["soc_max"]                                           # Speicherkapazität = max. Speicherinhalt

    inv = {  # Mrd. €
        "a_wp": wp_gw * 1e6 * c.wp_eur_per_kw / 1e9 if np.isfinite(wp_gw) else 0.0,
        "a_res": res_a_gw * 1e6 * c.residual_eur_per_kw / 1e9,
        "a_sto": 0.0,
        "b_wp": 0.0,
        "b_res": res_b_gw * 1e6 * c.residual_eur_per_kw / 1e9,
        "b_sto": cap_gwh * 1e6 * c.storage_eur_per_kwh / 1e9 if np.isfinite(cap_gwh) else 0.0,
    }
    af = {"wp": annuity_factor(c.interest_pct, c.life_wp_years),
          "res": annuity_factor(c.interest_pct, c.life_res_years),
          "sto": annuity_factor(c.interest_pct, c.life_sto_years)}

    def variant(prefix, load, rs, fee_ct, surplus_gwh, wp, res_gw):
        e = float(load.sum())
        energy = _energy_cost_mrd(load, rs, c)
        fees = e * fee_ct / 1e5
        invest = inv[f"{prefix}_wp"] + inv[f"{prefix}_res"] + inv[f"{prefix}_sto"]
        annuity = sum(inv[f"{prefix}_{x}"] * af[x] for x in ("wp", "res", "sto"))
        total = annuity + energy + fees
        heat = float(h_s.sum() + ww_s.sum())          # Nutzwärme Heizung + Warmwasser (COP 1 = Wärme)
        return {
            "load_twh": e / 1000.0, "surplus_twh": surplus_gwh / 1000.0, "gas_twh": (e - surplus_gwh) / 1000.0,
            "price_ct": energy * 1e5 / e if e > 0 else 0.0,
            "wp_gw": wp, "res_gw": res_gw, "cap_twh": (cap_gwh / 1000.0) if prefix == "b" else 0.0,
            "inv_wp": inv[f"{prefix}_wp"], "inv_res": inv[f"{prefix}_res"], "inv_sto": inv[f"{prefix}_sto"],
            "invest": invest, "annuity": annuity, "energy": energy, "fees": fees, "total": total,
            "per_ew": total * 1e9 / (n_s * 1e6) if n_s > 0 else 0.0,
            "ct_per_kwh_heat": total * 1e5 / heat if heat > 0 else 0.0,
        }

    surplus_a = float((load_a * (1.0 - rs_a)).sum())
    surplus_b = float((load_b * (1.0 - rs_b)).sum())
    return {
        "n_s": n_s, "heat_twh": float(h_s.sum() + ww_s.sum()) / 1000.0, "annuity_factor": af,
        "peak_res_z0_gw": peak_res_z0_gw, "peak_res_z2_gw": peak_res_z2_gw,
        "heat_kw_design": heat_kw, "wp_kw_el_per_ew": wp_kw_el,
        "a": variant("a", load_a, rs_a, c.fee_inflexible_ct, surplus_a, wp_gw, res_a_gw),
        "b": variant("b", load_b, rs_b, c.fee_flexible_ct, surplus_b, 0.0, res_b_gw),
    }
