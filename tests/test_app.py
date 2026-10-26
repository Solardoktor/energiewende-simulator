"""Smoke-Test der Streamlit-App mit synthetischen Daten (ohne Netzzugriff)."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from conftest import synthetic_installed, synthetic_raw, synthetic_temps
from energiewende import data

APP = str(Path(__file__).resolve().parent.parent / "SDR_TOOL_Energiewende-Simulator.py")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    st.cache_data.clear()

    def power(start, end, today, store=None):
        raw = synthetic_raw(start, end)
        return data.LoadResult(raw, [], ["synthetisch"])

    monkeypatch.setattr(data, "load_power_period", power)
    monkeypatch.setattr(data, "load_installed", lambda today, store=None: data.LoadResult(synthetic_installed()))
    monkeypatch.setattr(data, "load_temperatures",
                        lambda loc, s, e, today, store=None: data.LoadResult(synthetic_temps(s, e)))
    monkeypatch.setattr(data, "load_hgt_reference", lambda loc, store=None: (2300.0, []))


def test_app_laeuft_und_schalter_wirken():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 1
    first = at.dataframe[0].value.copy()

    at.sidebar.checkbox[1].check().run()          # Monatliche Skalierung aus
    assert not at.exception
    assert not at.dataframe[0].value.equals(first)

    at.sidebar.checkbox[2].uncheck().run()        # Pumpspeicher ein
    assert not at.exception

    at.sidebar.radio[0].set_value("Kalenderjahr").run()
    assert not at.exception
    at.sidebar.selectbox[0].set_value(2016).run()  # Jahr (vor dem Standort)
    assert not at.exception
