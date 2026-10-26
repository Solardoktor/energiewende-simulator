import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_installed, synthetic_raw, synthetic_temps
from energiewende import config, data, model


def run(p=model.Params(), raw=None, temps=None, hol=frozenset()):
    raw = synthetic_raw() if raw is None else raw
    inst, _ = data.daily_installed(synthetic_installed(), raw["date"])
    temps = synthetic_temps() if temps is None else temps
    df = model.compute_daily(raw, inst, temps, hol, p)
    return df, model.summarize(df, p)


# --- Zeiträume -------------------------------------------------------------
def test_runs_zyklisch_und_konsistent():
    m = np.array([1, 1, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 1], bool)
    assert sorted(l for _, l in model.find_runs(m, cyclic=True)) == [2, 6]
    assert sorted(l for _, l in model.find_runs(m, cyclic=False)) == [2, 3, 3]
    dates = pd.date_range("2024-01-01", periods=len(m))
    ps = model.periods(dates, m, np.arange(len(m)) * 1000.0)
    top = ps[0]
    assert top.days == 6 and top.wraps
    assert top.start == pd.Timestamp("2024-01-12") and top.end == pd.Timestamp("2024-01-03")
    assert top.energy_twh == pytest.approx(11 + 12 + 13 + 0 + 1 + 2)


@pytest.mark.parametrize("m,expected", [([0, 0, 0], []), ([1, 1, 1], [(0, 3)]), ([1, 0, 0], [(0, 1)])])
def test_runs_randfaelle(m, expected):
    assert model.find_runs(np.array(m, bool)) == expected


# --- Bilanz & Kennzahlen ---------------------------------------------------
def test_identitaeten_ee_anteil_und_co2():
    p = model.Params(add_ev_mio=10, add_heating_mio=20, add_dhw_mio=10)
    df, S = run(p)
    s = S["sums_gwh"]
    covered = np.minimum(df["ee_day"], df["total_load_gwh"]).sum()
    assert S["ee_share"] == pytest.approx(covered / s["total_load_gwh"])
    weighted = sum(S["co2_by_category"][c] * s[c] for c in model.LOAD_COLS) / s["total_load_gwh"]
    assert weighted == pytest.approx(S["co2_mean"])
    assert s["ee_day"] == pytest.approx(s["solar_scaled"] + s["wind_onshore_scaled"]
                                        + s["wind_offshore_scaled"] + s["other_ee"])
    assert (df["superpower_day"] - df["residual_day"]).to_numpy() == pytest.approx(df["ee_minus_load"].to_numpy())


def test_pumpspeicher_schalter_und_whitelist():
    off, _ = run(model.Params(include_pumped_storage=False))
    on, _ = run(model.Params(include_pumped_storage=True))
    assert off["other_ee"].iloc[0] == pytest.approx(110 + 50 + 3 + 0.5 + 0.5 * 12)   # ohne "others"
    assert (on["other_ee"] - off["other_ee"]).iloc[0] == pytest.approx(25 - 32)


def test_skalierung_aus_und_an():
    df, _ = run(model.Params(scaling_enabled=False))
    raw = synthetic_raw()
    assert df["solar_scaled"].to_numpy() == pytest.approx(raw["solar"].to_numpy())
    df, _ = run(model.Params(target_solar=200.0))
    assert df["solar_scaled"].to_numpy() == pytest.approx(
        (raw["solar"] * 200.0 / df["installed_solar"]).to_numpy())


def test_mobilitaet_jahresmenge_und_wochenendanteil():
    hol = frozenset({pd.Timestamp("2024-10-03").date()})
    p0 = model.Params(add_ev_mio=10, weekend_share=0.0)
    p5 = model.Params(add_ev_mio=10, weekend_share=0.5)
    d0, _ = run(p0, hol=hol)
    d5, _ = run(p5, hol=hol)
    total = 10 * config.KWH_PER_EV_PER_YEAR * 366 / 365
    assert d0["mobility_gwh"].sum() == pytest.approx(total)
    assert d5["mobility_gwh"].sum() == pytest.approx(total)
    we = d5["is_weekend_or_holiday"]
    assert d5.loc[we, "mobility_gwh"].sum() > d0.loc[we, "mobility_gwh"].sum()
    share_0 = d0.loc[we, "mobility_gwh"].sum() / total
    share_5 = d5.loc[we, "mobility_gwh"].sum() / total
    assert share_5 == pytest.approx(0.5 + 0.5 * share_0, rel=1e-6)
    assert d0.loc[d0["date"] == "2024-10-03", "is_weekend_or_holiday"].item()


def test_heizung_normaljahr_entspricht_jahreswert():
    temps = synthetic_temps()
    hgt = np.maximum(0, 15 - temps["temp_mean"]).sum()
    p = model.Params(add_heating_mio=10, share_air_hp=0, share_water_hp=0, hgt_reference=hgt)
    df, _ = run(p, temps=temps)
    assert df["heating_gwh"].sum() == pytest.approx(10 * config.HEAT_KWH_PER_PERSON_YEAR)
    # wärmeres Jahr → weniger Heizenergie
    p2 = model.Params(add_heating_mio=10, share_air_hp=0, share_water_hp=0, hgt_reference=hgt * 1.2)
    df2, _ = run(p2, temps=temps)
    assert df2["heating_gwh"].sum() == pytest.approx(10 * config.HEAT_KWH_PER_PERSON_YEAR / 1.2)


def test_heizung_fallback_ohne_temperatur():
    p = model.Params(add_heating_mio=10, share_air_hp=0, share_water_hp=0)
    df, S = run(p, temps=pd.DataFrame(columns=["date", "temp_mean"]))
    assert df["heating_gwh"].sum() == pytest.approx(10 * config.HEAT_KWH_PER_PERSON_YEAR * 366 / 365)
    assert S["hgt"] is None and S["avg_temp"] is None


def test_waermepumpe_senkt_strombedarf():
    a, _ = run(model.Params(add_heating_mio=10, share_air_hp=0, share_water_hp=0))
    b, _ = run(model.Params(add_heating_mio=10, share_air_hp=0.6, share_water_hp=0.1))
    assert 0.3 < b["heating_gwh"].sum() / a["heating_gwh"].sum() < 0.7


def test_fehlende_lastserie():
    raw = synthetic_raw().drop(columns=["load_incl_self_consumption"])
    with pytest.raises(KeyError):
        run(raw=raw)
