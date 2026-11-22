"""Langzeit-Wärmespeicher: Zustände Z1 (direktelektrisch) und Z2 (mit Speicher).

Z0 = Referenz (Speicher-Haushalte mit Wärmepumpen-Mix) ist die Rechnung aus
``model.compute_daily``. Diese Datei baut darauf auf:

* Z1: Speicher-Haushalte heizen direktelektrisch (COP 1), Warmwasser +50 %, nicht verschoben.
* Z2: Wärmebedarf der Speicher-Haushalte wird per Rückwärtsverfahren von Tagen mit
  Residuallast auf frühere Tage mit Überschuss (nach Elektrolyse) verschoben.

Alle Energien in GWh je Tag; Strom = Wärme (COP 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config
from .model import BASE_LOAD_COLS, Params, _apply_balance, _finish_balance, find_runs, german_number as gn, periods, summarize

EPS = 1e-9


# ---------------------------------------------------------------------------
# Rechenkern (testbar mit kleinen Arrays)
# ---------------------------------------------------------------------------
@dataclass
class Dispatch:
    storage_in: np.ndarray
    out_dhw: np.ndarray
    out_heat: np.ndarray
    uncovered: np.ndarray       # nicht deckbarer Speicherbedarf (bleibt Residuallast)
    soc: np.ndarray             # Speicherinhalt am Tagesende
    loss: np.ndarray            # Tagesverlust
    surplus_left: np.ndarray    # verbleibender Überschuss nach Einspeichern
    prev_year: float            # notwendiger Inhalt am Ende des Vortags von Tag 1
    potential_end: float        # mögliche Befüllung am Zeitraumende (nur ausgewiesen)
    durations: list = field(default_factory=list)   # (Tage, eingespeicherte Energie)
    start_content: float = 0.0  # stationäres Jahr: Inhalt am Jahreswechsel (Beginn = Ende)


def dispatch(residual, surplus, ww_s, h_s, cap_out, cap_in, loss: float, capacity: float,
             cyclic: bool = False) -> Dispatch:
    """Rückwärtsverfahren mit Kapazitätsgrenze.

    residual, surplus: Residuallast bzw. Überschuss nach Elektrolyse im Zustand Z1.
    ww_s, h_s: speicherfähiger Tagesbedarf Warmwasser bzw. Heizung (direktelektrisch).
    cap_out: Entladegrenze je Tag; cap_in: Ladegrenze je Tag. loss: Verlust je Tag (Anteil des Inhalts).
    capacity: Speicherkapazität (GWh) = Bemessungsleistung × Bemessungs-Speicherdauer; der Speicherinhalt
    überschreitet sie an keinem Tag. Die Verweildauer einzelner Energiemengen ist nicht begrenzt.
    cyclic: stationäres Jahr – die Rückwärtssuche darf über den Zeitraumbeginn hinaus auf die
    Überschüsse am Zeitraumende zugreifen; es gibt dann weder Inhalt aus dem Vorjahr noch Potenzial.
    """
    R = np.asarray(residual, float)
    n = len(R)
    arrays = [R, np.array(surplus, float), np.asarray(ww_s, float), np.asarray(h_s, float),
              np.array(np.broadcast_to(np.asarray(cap_out, float), R.shape)),
              np.array(np.broadcast_to(np.asarray(cap_in, float), R.shape))]
    shift = 0
    if cyclic and n:
        # Jahr so drehen, dass es mit der längsten Überschussperiode beginnt (Speicher dort typisch leer)
        runs = find_runs(arrays[1] > EPS, cyclic=True)
        if runs:
            shift = max(runs, key=lambda r: r[1])[0]
        arrays = [np.roll(a, -shift) for a in arrays]
    d = _dispatch_core(*arrays, float(loss), float(capacity), cyclic)
    if shift:
        for name in ("storage_in", "out_dhw", "out_heat", "uncovered", "soc", "loss", "surplus_left"):
            setattr(d, name, np.roll(getattr(d, name), shift))
    return d


def _dispatch_core(R, U, WW, H, cap_out, cap_in, loss, capacity, cyclic) -> Dispatch:
    """Kern in gedrehten Koordinaten.

    Jede Energiemenge wird in „Wert am Ausspeichertag“ y gerechnet. Eine Einspeicherung am Tag c für
    den Ausspeichertag d erhöht den Inhalt am Ende der Tage t = c … d−1 um y / keep^(d−t). Die
    Kapazitätsgrenze verlangt daher y ≤ min_t (Kapazität − Inhalt(t)) · keep^(d−t); dieser Wert hängt
    nicht von c ab und wird beim Zurückgehen fortlaufend aktualisiert.
    """
    n, keep = len(R), 1.0 - loss
    IN, OW, OH, unc = (np.zeros(n) for _ in range(4))
    soc = np.zeros(n)                       # Inhalt am Tagesende aus allen zugeordneten Paketen
    prev_year, potential, durations = 0.0, 0.0, []
    cap = capacity if capacity > 0 else 0.0

    def add_packet(c: int, k: int, x: float):
        """Paket x am Ende von Tag c eingespeichert, k Tage später ausgespeichert."""
        idx = (c + np.arange(k)) % n
        np.add.at(soc, idx, x * keep ** np.arange(k))

    i = n - 1
    if not cyclic:
        # Endphase: Überschuss nach dem letzten Defizittag → nur Speicherpotenzial (höchstens Kapazität)
        while i >= 0 and R[i] <= EPS:
            if U[i] > EPS:
                potential += min(U[i], cap_in[i]) * keep ** (n - 1 - i)
            i -= 1
        potential = min(potential, cap)

    # Ausspeichern rückwärts, jeweils Einspeichern im nächstgelegenen früheren Überschuss
    while i >= 0:
        if R[i] > EPS:
            ow = min(WW[i], R[i], cap_out[i])
            oh = max(0.0, min(H[i], R[i] - ow, cap_out[i] - ow))
            need = ow + oh                          # Wert am Ausspeichertag
            bound = np.inf                          # freie Kapazität im Fenster, in Werten am Tag i
            max_k = (n - 1) if cyclic else (i + 1)
            for k in range(1, max_k + 1):
                c = i - k
                if c < 0 and not cyclic:            # vor Zeitraumbeginn → Inhalt aus Vorjahr (Ende Tag −1)
                    y = min(need, max(0.0, min(bound, (cap - prev_year) * keep ** (i + 1))))
                    if y > EPS:
                        prev_year += y / keep ** (i + 1)
                        np.add.at(soc, np.arange(i), y / keep ** (i - np.arange(i)))
                        durations.append((k, y / keep ** (i + 1)))
                        need -= y
                    break
                cm = c % n
                bound = min(bound, (cap - soc[cm]) * keep ** k)
                if bound <= EPS:
                    break
                y = min(need, bound, min(U[cm], cap_in[cm] - IN[cm]) * keep ** k)
                if y > EPS:
                    x = y / keep ** k
                    IN[cm] += x
                    U[cm] -= x
                    add_packet(cm, k, x)
                    durations.append((k, x))
                    bound -= y
                    need -= y
                if need <= EPS:
                    need = 0.0
                    break
            if need > EPS:                          # nicht deckbar: erst Heizung, dann WW kürzen
                cut = min(oh, need)
                oh -= cut
                ow -= min(ow, need - cut)
            OW[i], OH[i] = ow, oh
            unc[i] = need if need > EPS else 0.0
        i -= 1

    # Tagesverluste aus dem Inhalt am Vortagesende
    prev_end = np.roll(soc, 1)
    prev_end[0] = soc[-1] if cyclic else prev_year
    lossd = prev_end * (1.0 - keep)
    start_content = float(soc[-1]) if cyclic else 0.0
    return Dispatch(IN, OW, OH, unc, soc, lossd, U, prev_year, potential, durations, start_content)


# ---------------------------------------------------------------------------
# Zustände Z1 und Z2
# ---------------------------------------------------------------------------
@dataclass
class StorageResult:
    active: bool
    notes: list
    z1: pd.DataFrame | None = None
    z2: pd.DataFrame | None = None
    summary_z1: dict | None = None
    summary_z2: dict | None = None
    kpi: dict = field(default_factory=dict)


def heat_per_ew_day(temp: float, hgt_ref: float) -> float:
    """Tageswärmebedarf je EW (kWh) bei Tagesmitteltemperatur ``temp``."""
    return config.HEAT_KWH_PER_PERSON_YEAR * max(0.0, config.HGT_BASE_TEMP - temp) / hgt_ref


def compute_storage(df0: pd.DataFrame, p: Params) -> StorageResult:
    """Z1 und Z2 aus der Referenzrechnung Z0 (``df0`` aus ``model.compute_daily``)."""
    s = float(p.storage_share)
    if s <= 0:
        return StorageResult(False, [])
    if p.add_heating_mio <= 0:
        return StorageResult(False, ["Langzeit-Wärmespeicher ohne Wirkung: keine zusätzlichen Heizungs-Einwohner "
                                     f"über dem Bestand ({config.CURRENT_HEATING} Mio.)."])
    n_s = p.add_heating_mio * s                                  # Speicher-Einwohner (Mio.)
    if p.add_dhw_mio + 1e-9 < n_s:
        return StorageResult(False, [
            f"Für {gn(n_s)} Mio. EW mit Langzeit-Wärmespeicher sind mindestens "
            f"{gn(config.CURRENT_DHW + n_s)} Mio. EW elektrische Warmwasserbereitung nötig "
            f"(eingestellt: {gn(config.CURRENT_DHW + p.add_dhw_mio)} Mio.). Speicher wird nicht berechnet."])

    # --- Z1: Speicher-Haushalte direktelektrisch, nicht verschoben
    z1 = df0.copy()
    mix = (p.share_direct + p.share_air_hp / df0["cop_air"] + p.share_water_hp / df0["cop_water"]).to_numpy()
    h_s = df0["heat_demand_gwh"].to_numpy() * s
    ww_ref = df0["dhw_gwh"].to_numpy() * n_s / p.add_dhw_mio
    ww_s = config.STORAGE_DHW_FACTOR * ww_ref
    z1["storage_h_s"] = h_s
    z1["storage_ww_s"] = ww_s
    z1["extra_heat_gwh"] = h_s * (1.0 - mix)
    z1["extra_dhw_gwh"] = ww_s - ww_ref
    z1["heating_gwh"] = df0["heating_gwh"] + z1["extra_heat_gwh"]
    z1["dhw_gwh"] = df0["dhw_gwh"] + z1["extra_dhw_gwh"]
    _apply_balance(z1, p)

    longest = periods(z1["date"], z1["residual_day"] > 0, z1["residual_day"], cyclic=True)
    if longest and longest[0].days > config.MAX_STORAGE_DAYS:
        return StorageResult(False, [
            f"Langzeit-Wärmespeicherung nicht sinnvoll: zu geringer EE-Ausbaustand – Superpower zu selten "
            f"(längste Dunkelflaute {longest[0].days} Tage > {config.MAX_STORAGE_DAYS} Tage)."],
            z1=z1, summary_z1=summarize(z1, p))

    # --- Leistungsgrenzen
    notes = []
    temps_ok = df0["temp_mean"].notna().any()
    if temps_ok:
        hgt = np.maximum(0.0, config.HGT_BASE_TEMP - df0["temp_mean"].to_numpy())
        ref = p.hgt_reference if p.hgt_reference else hgt.sum() * 365.0 / len(df0)
        q_design = heat_per_ew_day(p.design_temp, ref)                      # kWh/EW/Tag
        q_charge = heat_per_ew_day(config.STORAGE_CHARGE_DESIGN_TEMP, ref)
        cap_out = n_s * q_design + ww_s                                      # GWh/Tag
        cap_in = np.full(len(df0), n_s * q_charge + ww_s.max())
    else:
        q_design = q_charge = float("nan")
        cap_out = np.full(len(df0), np.inf)
        cap_in = np.full(len(df0), np.inf)
        notes.append("Ohne Temperaturdaten keine Lade-/Entladegrenzen.")

    capacity = float(np.max(cap_out)) * p.storage_days if temps_ok else float("inf")   # GWh
    # Für die Speicherbeladung verfügbarer Überschuss je nach Priorität:
    #  Elektrolyse zuerst → Überschuss nach Elektrolyse; Speicher zuerst → Überschuss vor Elektrolyse
    surplus_for_storage = (z1["superpower_day"] + z1["electrolysis_gwh"]) if p.storage_first \
        else z1["superpower_day"]
    disp = dispatch(z1["residual_day"], surplus_for_storage, ww_s, h_s, cap_out, cap_in,
                    p.storage_loss, capacity, cyclic=p.storage_cyclic)

    # --- Z2: mit Speicher
    z2 = z1.copy()
    out = disp.out_dhw + disp.out_heat
    z2["storage_surplus_avail"] = np.asarray(surplus_for_storage, float)
    z2["storage_residual_z1"] = z1["residual_day"].to_numpy()
    z2["storage_in_gwh"] = disp.storage_in
    z2["storage_out_dhw_gwh"] = disp.out_dhw
    z2["storage_out_heat_gwh"] = disp.out_heat
    z2["storage_uncovered_gwh"] = disp.uncovered
    z2["storage_loss_gwh"] = disp.loss
    z2["storage_soc_gwh"] = disp.soc
    z2["storage_cap_out_gwh"] = cap_out
    z2["dhw_gwh"] = z1["dhw_gwh"] - disp.out_dhw
    z2["heating_gwh"] = z1["heating_gwh"] - disp.out_heat
    load_base = z2[list(BASE_LOAD_COLS)].sum(axis=1)
    if p.storage_first:
        # Elektrolyse erhält nur den Überschuss, der nach der Speicherbeladung übrig bleibt
        z2["electrolysis_gwh"] = np.minimum(max(0.0, p.electrolysis_gw) * 24.0,
                                            np.maximum(0.0, z2["ee_day"] - load_base - disp.storage_in))
    # (Elektrolyse zuerst: unverändert aus Z1; Speicher IN ≤ Überschuss nach Elektrolyse)
    z2["total_load_gwh"] = load_base + z2["electrolysis_gwh"] + disp.storage_in
    _finish_balance(z2)

    s0, s1, s2 = summarize(df0, p), summarize(z1, p), summarize(z2, p)
    sum_in = disp.storage_in.sum()
    durs = np.array(disp.durations) if disp.durations else np.zeros((0, 2))
    i_max = int(np.argmax(disp.soc)) if len(disp.soc) else 0
    kpi = {
        "n_s": n_s,
        "out_dhw": disp.out_dhw.sum(), "out_heat": disp.out_heat.sum(), "in": sum_in,
        "loss": disp.loss.sum(),
        "extra_heat": z1["extra_heat_gwh"].sum(), "extra_dhw": z1["extra_dhw_gwh"].sum(),
        "heat_z0": s0["sums_gwh"]["heating_gwh"], "dhw_z0": s0["sums_gwh"]["dhw_gwh"],
        "elyse_change": s2["sums_gwh"]["electrolysis_gwh"] - s0["sums_gwh"]["electrolysis_gwh"],
        "storage_first": bool(p.storage_first),
        "total_z0": s0["sums_gwh"]["total_load_gwh"], "total_z2": s2["sums_gwh"]["total_load_gwh"],
        "uncovered": disp.uncovered.sum(),
        "prev_year": disp.prev_year, "potential_end": disp.potential_end,
        "self_sufficient": disp.potential_end + EPS >= disp.prev_year,
        "soc_max": float(disp.soc.max()) if len(disp.soc) else 0.0,
        "soc_max_date": df0["date"].iloc[i_max] if len(df0) else None,
        "soc_end": float(disp.soc[-1]) if len(disp.soc) else 0.0,
        "soc_start": (float(disp.soc[-1]) if p.storage_cyclic else disp.prev_year) if len(disp.soc) else 0.0,
        "cyclic": bool(p.storage_cyclic),
        "q_design": q_design, "q_charge": q_charge,
        "p_design_gw": float(np.max(cap_out)) / 24.0 if temps_ok else float("nan"),
        "p_in_max_gw": float(disp.storage_in.max()) / 24.0, "p_in_cap_gw": float(cap_in[0]) / 24.0,
        "p_out_max_gw": float(out.max()) / 24.0, "p_out_cap_gw": float(np.max(cap_out)) / 24.0,
        "dur_max": int(durs[:, 0].max()) if len(durs) else 0,
        "dur_mean": float((durs[:, 0] * durs[:, 1]).sum() / durs[:, 1].sum()) if len(durs) and durs[:, 1].sum() > 0 else 0.0,
        "days_param": int(p.storage_days),
        "capacity": capacity,
    }
    return StorageResult(True, notes, z1, z2, s1, s2, kpi)
