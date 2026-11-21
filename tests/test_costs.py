from dataclasses import replace

import numpy as np
import pytest

from conftest import synthetic_installed, synthetic_raw, synthetic_temps
from energiewende import data, model
from energiewende.costs import CostParams, annuity_factor, compute_costs
from energiewende.storage import compute_storage

P = model.Params(target_solar=400, target_wind_onshore=180, target_wind_offshore=70, add_heating_mio=30,
                 add_dhw_mio=30, share_air_hp=0.6, share_water_hp=0.1, hgt_reference=2300,
                 storage_share=0.5, storage_days=30, design_temp=-7)


def _run(p=P, c=CostParams()):
    raw = synthetic_raw()
    inst, _ = data.daily_installed(synthetic_installed(), raw["date"])
    z0 = model.compute_daily(raw, inst, synthetic_temps(), frozenset(), p)
    r = compute_storage(z0, p)
    s0 = model.summarize(z0, p)
    s2 = r.summary_z2 if r.active else s0
    return z0, r, compute_costs(z0, r, p, c, s0["peak_residual"][0], s2["peak_residual"][0])


def test_annuitaet():
    assert annuity_factor(0, 20) == pytest.approx(0.05)
    assert annuity_factor(3, 20) == pytest.approx(0.067216, rel=1e-4)


def test_inaktiv_ohne_speicher():
    _, _, c = _run(replace(P, storage_share=0))
    assert c is None


def test_strombezug_und_kostenbausteine():
    z0, r, c = _run()
    a, b, k = c["a"], c["b"], r.kpi
    # Mehrverbrauch Speicherheizung = Mehrbedarf direktelektrisch + Ladeenergie − Speicherentladung
    assert (b["load_twh"] - a["load_twh"]) * 1000 == pytest.approx(
        k["extra_heat"] + k["extra_dhw"] + k["in"] - k["out_dhw"] - k["out_heat"], rel=1e-9)
    # Summen der Bausteine, Netzentgelte, Annuität
    for v, fee in ((a, 17.0), (b, 10.0)):
        assert v["total"] == pytest.approx(v["annuity"] + v["energy"] + v["fees"])
        assert v["fees"] == pytest.approx(v["load_twh"] * 1000 * fee / 1e5)
        assert v["invest"] == pytest.approx(v["inv_wp"] + v["inv_res"] + v["inv_sto"])
        assert 1.0 <= v["price_ct"] <= 18.0
    assert a["inv_sto"] == 0 and b["inv_wp"] == 0 and a["inv_wp"] > 0 and b["inv_sto"] > 0
    assert b["inv_sto"] == pytest.approx(k["soc_max"] * 1e6 * 15 / 1e9)          # max. Speicherinhalt, 15 €/kWh
    for v in (a, b):
        assert v["surplus_twh"] + v["gas_twh"] == pytest.approx(v["load_twh"])
    # zusätzliche Residualleistung = Differenz der Höchstwerte, der höheren Variante angerechnet
    delta = c["peak_res_z2_gw"] - c["peak_res_z0_gw"]
    assert b["res_gw"] - a["res_gw"] == pytest.approx(delta) and min(a["res_gw"], b["res_gw"]) == 0
    assert c["peak_res_z0_gw"] == pytest.approx(z0["residual_day"].max() / 24)
    assert c["peak_res_z2_gw"] == pytest.approx(r.z2["residual_day"].max() / 24)
    # getrennte Nutzungsdauern
    from energiewende.costs import annuity_factor as af
    assert b["annuity"] == pytest.approx(b["inv_res"] * af(3, 40) + b["inv_sto"] * af(3, 50))
    assert a["annuity"] == pytest.approx(a["inv_wp"] * af(3, 20) + a["inv_res"] * af(3, 40))
    # Wärmepumpenleistung bei Auslegung: q(−7 °C)/24 × (0,6/COP_L + 0,1/COP_S) je EW
    assert c["wp_kw_el_per_ew"] == pytest.approx(
        10000 * 22 / 2300 / 24 * (0.6 / 1.8 + 0.1 / (4.0 - 0.21)), rel=1e-6)


def test_preise_null_ergibt_nur_investitionen():
    zero = CostParams(price_gas_ct=0, price_surplus_ct=0, fee_inflexible_ct=0, fee_flexible_ct=0)
    _, _, c = _run(c=zero)
    for v in (c["a"], c["b"]):
        assert v["energy"] == 0 and v["fees"] == 0 and v["total"] == pytest.approx(v["annuity"])


def test_einheitlicher_preis_ergibt_diesen_mittelwert():
    flat = CostParams(price_gas_ct=12, price_surplus_ct=12)
    _, _, c = _run(c=flat)
    assert c["a"]["price_ct"] == pytest.approx(12) and c["b"]["price_ct"] == pytest.approx(12)
    assert np.isfinite(c["a"]["per_ew"]) and c["a"]["per_ew"] > 0
