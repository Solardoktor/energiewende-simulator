from dataclasses import replace

import numpy as np
import pytest

from conftest import synthetic_installed, synthetic_raw, synthetic_temps
from energiewende import data, model
from energiewende.storage import compute_storage, dispatch

INF = np.inf


# --- Rechenkern an Hand-Beispielen -----------------------------------------
def test_dispatch_handbeispiel_mit_verlust_und_dauergrenze():
    # Tage 0..3: Überschuss an 0, 1, 3; Residuallast an Tag 2
    d = dispatch(residual=[0, 0, 4, 0], surplus=[5, 5, 0, 3], ww_s=[1] * 4, h_s=[3] * 4,
                 cap_out=INF, cap_in=INF, loss=0.5, max_days=2)
    assert d.potential_end == pytest.approx(3.0)                 # Tag 3, letzter Tag
    assert d.storage_in.tolist() == pytest.approx([5, 5, 0, 0])  # 4 / 0,5 = 8 → 5 an Tag 1, 3 / 0,5 = 6 → 5 an Tag 0
    assert d.out_dhw[2] == pytest.approx(1.0)
    assert d.out_heat[2] == pytest.approx(3 - 0.25)              # Rest 1 nach 2 Tagen: 1 × 0,5² = 0,25 nicht deckbar
    assert d.uncovered[2] == pytest.approx(0.25)
    assert d.soc.tolist() == pytest.approx([5, 7.5, 0, 0])
    assert d.prev_year == 0


def test_dispatch_vorjahr():
    d = dispatch(residual=[4, 0], surplus=[0, 0], ww_s=[1, 1], h_s=[3, 3],
                 cap_out=INF, cap_in=INF, loss=0.5, max_days=5)
    assert d.prev_year == pytest.approx(8.0)                     # 4 GWh am Tag 0 nach einem Verlusttag
    assert d.soc[0] == pytest.approx(0.0)


def test_dispatch_ladegrenze_und_entladegrenze():
    # Entladegrenze 6 begrenzt den Bedarf 10; Ladegrenze 4 begrenzt das Einspeichern an Tag 0
    d = dispatch(residual=[0, 10], surplus=[20, 0], ww_s=[1, 1], h_s=[9, 9],
                 cap_out=[100, 6], cap_in=4, loss=0.0, max_days=1)
    assert d.storage_in[0] == pytest.approx(4)
    assert d.out_dhw[1] + d.out_heat[1] == pytest.approx(4) and d.uncovered[1] == pytest.approx(2)
    # mit längerer Speicherdauer reicht die Suche vor den Zeitraumbeginn → Rest aus Vorjahr
    d = dispatch(residual=[0, 10], surplus=[20, 0], ww_s=[1, 1], h_s=[9, 9],
                 cap_out=[100, 6], cap_in=4, loss=0.0, max_days=3)
    assert d.out_dhw[1] + d.out_heat[1] == pytest.approx(6) and d.prev_year == pytest.approx(2)


def test_dispatch_dauer_null():
    d = dispatch(residual=[0, 5], surplus=[9, 0], ww_s=[1, 1], h_s=[4, 4],
                 cap_out=INF, cap_in=INF, loss=0.0, max_days=0)
    assert d.storage_in.sum() == 0 and d.out_dhw.sum() + d.out_heat.sum() == 0


# --- Integration mit synthetischem Jahr ----------------------------------------
def _z0(p):
    raw = synthetic_raw()
    inst, _ = data.daily_installed(synthetic_installed(), raw["date"])
    return model.compute_daily(raw, inst, synthetic_temps(), frozenset(), p)


BASE = model.Params(target_solar=400, target_wind_onshore=180, target_wind_offshore=70,
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
    assert k["dur_max"] <= p.storage_days
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
