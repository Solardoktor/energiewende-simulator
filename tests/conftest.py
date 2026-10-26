import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from energiewende import config  # noqa: E402


def v2_power_json(start_local: str, end_local: str, freq: str = "15min", mw: dict | None = None,
                  drop: slice | None = None, naive: bool = False) -> dict:
    """Synthetischer v2-Envelope mit lokalen ISO-Zeitstempeln inkl. Offset."""
    idx = pd.date_range(start_local, end_local, freq=freq, tz=config.TIMEZONE, inclusive="left")
    if drop is not None:
        idx = idx.delete(drop)
    mw = mw or {"load_incl_self_consumption": 1000.0}
    recs = []
    for t in idx:
        ts = t.tz_localize(None).isoformat() if naive else t.isoformat()
        recs.append({"timestamp": ts, "values": dict(mw)})
    return {"unit": "MW", "data": recs}


def synthetic_raw(start="2024-01-01", end="2024-12-31", seed=1) -> pd.DataFrame:
    """Tageswerte (GWh) im Format von data.load_power_period."""
    rng = np.random.default_rng(seed)
    d = pd.date_range(start, end, freq="D")
    doy = d.dayofyear.to_numpy()
    season = np.cos(2 * np.pi * (doy - 172) / 365)          # Sommer +1, Winter −1
    return pd.DataFrame({
        "date": d,
        "load_incl_self_consumption": 1300 - 120 * season,
        "solar": np.clip(200 + 180 * season + rng.normal(0, 40, len(d)), 5, None),
        "wind_onshore": np.clip(300 - 120 * season + rng.normal(0, 200, len(d)), 10, None),
        "wind_offshore": np.clip(70 - 25 * season + rng.normal(0, 40, len(d)), 2, None),
        "biomass": 110.0, "hydro_run_of_river": 50.0, "hydro_water_reservoir": 3.0,
        "geothermal": 0.5, "waste": 12.0, "others": 8.0, "fossil_gas": 150.0,
        "hydro_pumped_storage": 25.0, "hydro_pumped_storage_consumption": -32.0,
        "coverage": 1.0,
    })


def synthetic_installed() -> pd.DataFrame:
    months = pd.period_range("2014-12", "2026-12", freq="M")
    k = np.arange(len(months))
    return pd.DataFrame({"solar": 38 + 0.75 * k, "wind_onshore": 40 + 0.25 * k,
                         "wind_offshore": 3 + 0.06 * k,
                         "month_end": months.to_timestamp(how="end").normalize()})


def synthetic_temps(start="2024-01-01", end="2024-12-31") -> pd.DataFrame:
    d = pd.date_range(start, end, freq="D")
    return pd.DataFrame({"date": d, "temp_mean": 9.5 - 9.5 * np.cos(2 * np.pi * (d.dayofyear - 20) / 365)})


@pytest.fixture
def raw():
    return synthetic_raw()
