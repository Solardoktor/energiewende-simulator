from dataclasses import replace

import numpy as np
import pytest

from conftest import synthetic_installed, synthetic_raw, synthetic_temps
from energiewende import data, model
from energiewende.storage import compute_storage, dispatch

INF = np.inf


# --- Rechenkern an Hand-Beispielen -----------------------------------------
def test_dispatch_handbeispiel_ohne_kapazitaetsgrenze():
    # Tage 0..3: Überschuss an 0, 1, 3; Residuallast an Tag 2; Verlust 50 %/Tag
    d = dispatch(residual=[0, 0, 4, 0], surplus=[5, 5, 0, 3], ww_s=[1] * 4, h_s=[3] * 4,
                 cap_out=INF, cap_in=INF, loss=0.5, capacity=INF)
    assert d.potential_end == pytest.approx(3.0)                 # Tag 3, letzter Tag
    assert d.storage_in.tolist() == pytest.approx([5, 5, 0, 0])
    assert d.prev_year == pytest.approx(2.0)                     # Rest 0,25 am Tag 2 → 0,25 / 0,5³ am Ende Tag −1
    assert d.out_dhw[2] + d.out_heat[2] == pytest.approx(4) and d.uncovered.sum() == 0
    assert d.soc.tolist() == pytest.approx([6, 8, 0, 0])


def test_dispatch_kapazitaetsgrenze():
    d = dispatch(residual=[0, 0, 4, 0], surplus=[5, 5, 0, 3], ww_s=[1] * 4, h_s=[3] * 4,
                 cap_out=INF, cap_in=INF, loss=0.5, capacity=6.0)
    assert d.soc.max() <= 6.0 + 1e-9
    assert d.storage_in.tolist() == pytest.approx([2, 5, 0, 0])
    assert d.out_dhw[2] + d.out_heat[2] == pytest.approx(3) and d.uncovered[2] == pytest.approx(1)
    assert d.soc.tolist() == pytest.approx([2, 6, 0, 0])


def test_dispatch_lange_verweildauer_erlaubt():
    # Überschuss nur 100 Tage vor dem Defizit: ohne Dauergrenze wird er genutzt
    n = 120
    R = [0.0] * n; U = [0.0] * n
    U[0], R[100] = 10.0, 4.0
    d = dispatch(R, U, [1] * n, [3] * n, INF, INF, loss=0.0, capacity=5.0)
    assert d.storage_in[0] == pytest.approx(4) and d.uncovered.sum() == 0


def test_dispatch_vorjahr():
    d = dispatch(residual=[4, 0], surplus=[0, 0], ww_s=[1, 1], h_s=[3, 3],
                 cap_out=INF, cap_in=INF, loss=0.5, capacity=INF)
    assert d.prev_year == pytest.approx(8.0)                     # 4 GWh am Tag 0 nach einem Verlusttag
    assert d.soc[0] == pytest.approx(0.0)


def test_dispatch_lade_und_entladegrenze():
    d = dispatch(residual=[0, 10], surplus=[20, 0], ww_s=[1, 1], h_s=[9, 9],
                 cap_out=[100, 6], cap_in=4, loss=0.0, capacity=INF)
    assert d.storage_in[0] == pytest.approx(4)                   # Ladegrenze
    assert d.out_dhw[1] + d.out_heat[1] == pytest.approx(6)      # Entladegrenze; Rest 2 aus Vorjahr
    assert d.prev_year == pytest.approx(2)


def test_dispatch_kapazitaet_null():
    d = dispatch(residual=[0, 5], surplus=[9, 0], ww_s=[1, 1], h_s=[4, 4],
                 cap_out=INF, cap_in=INF, loss=0.0, capacity=0.0)
    assert d.storage_in.sum() == 0 and d.out_dhw.sum() + d.out_heat.sum() == 0


# --- Integration mit synthetischem Jahr ----------------------------------------
def _z0(p):
    raw = synthetic_raw()
    inst, _ = data.daily_installed(synthetic_installed(), raw["date"])
    return model.compute_daily(raw, inst, synthetic_temps(), frozenset(), p)


BASE = model.Params(target_solar=400, target_wind_onshore=180, target_wind_offshore=70, storage_cyclic=False,
                    add_heating_mio=30, add_dhw_mio=30, share_air_hp=0.5, share_water_hp=0.2,
                    hgt_reference=2300, storage_share=0.5, storage_days=60)


def test_anteil_null_inaktiv_und_ww_pruefung():
    assert not compute_storage(_z0(replace(BASE, storage_share=0)), replace(BASE, storage_share=0)).active
    p = replace(BASE, add_dhw_mio=10)                             # 15 Mio. Speicher-EW > 10 Mio. zusätzl. WW
    r = compute_storage(_z0(p), p)
    assert not r.active and "mindestens 30,0 Mio." in r.notes[0]


@pytest.mark.parametrize("loss", [0.0, 0.01])
def test_energiebilanz_und_grenzen(loss):
    p = replace(BASE, storage_loss=loss)
    z0 = _z0(p)
    r = compute_storage(z0, p)
    assert r.active, r.notes
    z1, z2, k = r.z1, r.z2, r.kpi
    out = z2["storage_out_dhw_gwh"] + z2["storage_out_heat_gwh"]
    assert k["in"] > 0 and out.sum() > 0
    # Bilanz: Vorjahr + IN − Verluste − OUT = Endinhalt = 0; Inhalt nie negativ
    assert k["prev_year"] + k["in"] - k["loss"] - out.sum() == pytest.approx(0, abs=1e-6)
    assert z2["storage_soc_gwh"].min() >= -1e-6 and k["soc_end"] == pytest.approx(0, abs=1e-6)
    if loss == 0:
        assert k["prev_year"] + k["in"] == pytest.approx(out.sum())
    # Einspeichern nur aus Überschuss, Ausspeichern nur bis zur Residuallast
    assert (z2["storage_in_gwh"] <= z2["storage_surplus_avail"] + 1e-9).all()
    assert (out <= z2["storage_residual_z1"] + 1e-9).all()
    assert (z2["storage_in_gwh"][z2["storage_surplus_avail"] <= 0] == 0).all()
    assert (z2["storage_out_dhw_gwh"] <= z1["storage_ww_s"] + 1e-9).all()
    assert (z2["storage_out_heat_gwh"] <= z1["storage_h_s"] + 1e-9).all()
    assert z2["storage_soc_gwh"].max() <= k["capacity"] + 1e-6                # Kapazitätsgrenze
    # Z2: Residuallast sinkt genau um OUT, Superpower um IN
    assert z2["residual_day"].to_numpy() == pytest.approx((z1["residual_day"] - out).to_numpy())
    assert z2["superpower_day"].to_numpy() == pytest.approx((z1["superpower_day"] - z2["storage_in_gwh"]).to_numpy())


def test_mehrbedarf_z1_gegen_z0():
    p = BASE
    z0 = _z0(p)
    r = compute_storage(z0, p)
    z1 = r.z1
    assert (z1["extra_heat_gwh"] >= 0).all() and r.kpi["extra_heat"] > 0
    assert r.kpi["extra_dhw"] == pytest.approx(0.5 * z0["dhw_gwh"].sum() * 15 / 30)
    # Lasten ohne Elektrolyse: Z1 − Z0 = Mehrbedarf
    base_cols = list(model.BASE_LOAD_COLS)
    diff = z1[base_cols].sum(axis=1) - z0[base_cols].sum(axis=1)
    assert diff.to_numpy() == pytest.approx((z1["extra_heat_gwh"] + z1["extra_dhw_gwh"]).to_numpy())
    # Z0 hängt nicht vom Speicheranteil ab
    z0b = _z0(replace(p, storage_share=0))
    assert z0["total_load_gwh"].to_numpy() == pytest.approx(z0b["total_load_gwh"].to_numpy())


def test_dunkelflaute_pruefung():
    p = replace(BASE, target_solar=50, target_wind_onshore=20, target_wind_offshore=5)
    r = compute_storage(_z0(p), p)
    assert not r.active and "nicht sinnvoll" in r.notes[0]


# --- Stationäres Jahr -------------------------------------------------------
def test_dispatch_zyklisch_handbeispiel():
    # Defizit an Tag 0, Überschuss nur an Tag 3 (Zeitraumende) → zyklisch über den Jahreswechsel
    d = dispatch(residual=[4, 0, 0, 0], surplus=[0, 0, 0, 10], ww_s=[1] * 4, h_s=[3] * 4,
                 cap_out=INF, cap_in=INF, loss=0.5, capacity=INF, cyclic=True)
    assert d.prev_year == 0 and d.potential_end == 0
    assert d.storage_in.tolist() == pytest.approx([0, 0, 0, 8])     # 4 / 0,5 (ein Verlusttag)
    assert d.out_dhw[0] + d.out_heat[0] == pytest.approx(4)
    assert d.soc.tolist() == pytest.approx([0, 0, 0, 8])            # Ende = Beginn: 8
    linear = dispatch(residual=[4, 0, 0, 0], surplus=[0, 0, 0, 10], ww_s=[1] * 4, h_s=[3] * 4,
                      cap_out=INF, cap_in=INF, loss=0.5, capacity=INF, cyclic=False)
    assert linear.prev_year == pytest.approx(8) and linear.storage_in.sum() == 0


@pytest.mark.parametrize("loss", [0.0, 0.01])
def test_zyklisch_bilanz_geschlossen(loss):
    p = replace(BASE, storage_loss=loss, storage_cyclic=True)
    r = compute_storage(_z0(p), p)
    z2, k = r.z2, r.kpi
    out = (z2["storage_out_dhw_gwh"] + z2["storage_out_heat_gwh"]).sum()
    assert k["prev_year"] == 0 and k["potential_end"] == 0
    assert k["in"] - k["loss"] - out == pytest.approx(0, abs=1e-6)       # stationär: Beginn = Ende
    assert z2["storage_soc_gwh"].min() >= -1e-6
    assert k["soc_start"] == pytest.approx(z2["storage_soc_gwh"].iloc[-1])
    assert z2["storage_soc_gwh"].max() <= k["capacity"] + 1e-6
    # Mehrverbrauch vollständig sichtbar: Z2 − Z0 = Mehrbedarf + Elektrolyse-Änderung + Verluste
    assert k["total_z2"] - k["total_z0"] == pytest.approx(
        k["extra_heat"] + k["extra_dhw"] + k["elyse_change"] + k["loss"], abs=1e-6)


# --- Priorität Speicherbeladung vor Elektrolyse --------------------------------
@pytest.mark.parametrize("cyclic", [True, False])
def test_prioritaet_speicher_vor_elektrolyse(cyclic):
    base = replace(BASE, electrolysis_gw=20, storage_cyclic=cyclic, storage_days=60)
    pe, ps = replace(base, storage_first=False), replace(base, storage_first=True)
    re_, rs = compute_storage(_z0(pe), pe), compute_storage(_z0(ps), ps)
    assert re_.active and rs.active
    z1, z2 = rs.z1, rs.z2
    s_in, cap = z2["storage_in_gwh"], 20 * 24
    before = z1["superpower_day"] + z1["electrolysis_gwh"]           # Überschuss vor Elektrolyse
    assert (s_in <= before + 1e-9).all()
    # Elektrolyse bekommt nur den Rest nach der Speicherbeladung, höchstens 24 h × Leistung
    assert z2["electrolysis_gwh"].to_numpy() == pytest.approx(np.clip(np.minimum(cap, before - s_in), 0, None).to_numpy())
    assert z2["superpower_day"].to_numpy() == pytest.approx(np.clip(before - s_in - z2["electrolysis_gwh"], 0, None).to_numpy())
    # Residuallast sinkt genau um die Speicherentladung (wie bisher)
    out = z2["storage_out_dhw_gwh"] + z2["storage_out_heat_gwh"]
    assert z2["residual_day"].to_numpy() == pytest.approx((z1["residual_day"] - out).to_numpy())
    # Vergleich der Prioritäten: mehr Speicherladung, weniger Elektrolyse
    assert rs.kpi["in"] >= re_.kpi["in"] - 1e-6
    assert z2["electrolysis_gwh"].sum() <= re_.z2["electrolysis_gwh"].sum() + 1e-6
    assert rs.kpi["uncovered"] <= re_.kpi["uncovered"] + 1e-6
    # Bilanzen geschlossen, Kapazität eingehalten
    k = rs.kpi
    soc0 = k["soc_start"] if cyclic else k["prev_year"]
    assert soc0 + k["in"] - k["loss"] - out.sum() - k["soc_end"] == pytest.approx(0, abs=1e-6)
    assert z2["storage_soc_gwh"].max() <= k["capacity"] + 1e-6
    if cyclic:
        assert k["total_z2"] - k["total_z0"] == pytest.approx(
            k["extra_heat"] + k["extra_dhw"] + k["elyse_change"] + k["loss"], abs=1e-6)


def test_prioritaet_ohne_elektrolyse_gleich():
    p0 = replace(BASE, electrolysis_gw=0)
    a = compute_storage(_z0(p0), replace(p0, storage_first=False))
    b = compute_storage(_z0(p0), replace(p0, storage_first=True))
    assert a.z2["total_load_gwh"].to_numpy() == pytest.approx(b.z2["total_load_gwh"].to_numpy())
