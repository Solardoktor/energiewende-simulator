"""Plotly-Grafik der Tagesbilanz.

Jede Kategorie ist ein echtes Segment (``base`` = Summe der darunterliegenden
Kategorien, ``y`` = eigener Wert). Wird eine Kategorie über die Legende
ausgeblendet, entsteht eine Lücke – die übrigen Segmente behalten Lage und Farbe.
Erzeugung (halbtransparent, schraffiert) liegt bewusst über der Last, sodass sich
die Farben mischen.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Schriftgrößen der Grafiken (px)
FONT_BASE = 14
FONT_AXIS_TITLE = 16
FONT_TICK = 14
FONT_LEGEND = 13
FONT_NOTE = 12
GERMAN_SEPARATORS = ",."   # Dezimalkomma, Tausenderpunkt


def _axis(**kw) -> dict:
    """Achse mit einheitlichen Schriftgrößen und Gitter."""
    title = kw.pop("title", "")
    return dict(title=dict(text=title, font=dict(size=FONT_AXIS_TITLE)), tickfont=dict(size=FONT_TICK),
                showgrid=True, gridcolor="rgba(200,200,200,0.25)", **kw)


COLORS = {
    "superpower": "#FFF176",
    "base": "#808080",
    "mobility": "#FF99FF",
    "dhw": "#C2185B",
    "heating": "#d32f2f",
    "electrolysis": "#1565C0",   # Dunkelblau
    "storage_in": "#EF6C00",     # Orange
    "storage_out_dhw": "#F48FB1",   # Warmwasser heller
    "storage_out_heat": "#EF9A9A",  # Heizung heller
    "temperature": "#455A64",
}
""" AHO: GEN_STYLE = {   # (Füllfarbe halbtransparent, Musterfarbe)
    "solar": ("rgba(255,204,0,0.35)", "#FFCC00"),
    "wind_onshore": ("rgba(124,179,66,0.35)", "#7CB342"),
    "wind_offshore": ("rgba(56,142,60,0.35)", "#388E3C"),
    "other_ee": ("rgba(25,118,210,0.35)", "#1976D2"),
} """ 
GEN_STYLE = {   # (Füllfarbe halbtransparent, Musterfarbe)
    "solar": ("#FFCC00", "#FFCC00"),
    "wind_onshore": ("#7CB342", "#7CB342"),
    "wind_offshore": ("#388E3C", "#388E3C"),
    "other_ee": ("#4FC3F7", "#4FC3F7"),   # Lichtblau
}

def _seg(fig, x, y, base, name, hover_label, opacity=1.0, color=None, marker=None):
    fig.add_trace(go.Bar(
        x=x, y=y, base=base, name=name, opacity=opacity,
        marker=marker or dict(color=color),
        # Plotly zeigt bei Balken mit base im Hover base + y; daher Segmentwert über customdata
        customdata=y,
        hovertemplate=f"{hover_label}: %{{customdata:.1f}} GWh<extra></extra>",
    ))


""" AHO: def _hatched(key):
    fill, fg = GEN_STYLE[key]
    return dict(color=fill, pattern_shape="/", pattern_fgcolor=fg, pattern_size=5, pattern_solidity=0.4)
"""
def _hatched(key):
    base, fg = GEN_STYLE[key]
    return dict(
        color=base,
        pattern=dict(
            shape="/",
            fgcolor=fg,
            fgopacity=1.0,                 # Linien voll deckend
            bgcolor="rgba(0,0,0,0)",       # Hintergrund komplett durchsichtig
            fillmode="replace",
            size=5,
            solidity=0.5,
        ),
    )

def build_figure(df: pd.DataFrame, start: date, end: date, title: str, subtitle: str) -> go.Figure:
    x = df["date"]
    base, mob, dhw, heat = (df[c] for c in ("base_load_gwh", "mobility_gwh", "dhw_gwh", "heating_gwh"))
    other, off, on, sol = (df[c] for c in ("other_ee", "wind_offshore_scaled", "wind_onshore_scaled", "solar_scaled"))

    fig = go.Figure()

    # Superpower ganz hinten: volle EE-Höhe, sichtbar bleibt der Teil oberhalb der Last.
    # Hover zeigt EE − Last (negativ = Residualenergie).
    fig.add_trace(go.Bar(
        x=x, y=df["ee_day"], name="Superpower", marker_color=COLORS["superpower"], opacity=0.95,
        customdata=np.column_stack([df["ee_minus_load"], df["total_load_gwh"]]),
        hovertemplate="EE − Netzlast: %{customdata[0]:.1f} GWh<br>Netzlast: %{customdata[1]:.1f} GWh<extra></extra>",
    ))

    # Langzeit-Wärmespeicher ganz oben: Speicher IN (Netzbezug), darüber Speicher OUT (kein Netzbezug)
    if "storage_in_gwh" in df:
        elyse0 = df["electrolysis_gwh"]
        top = base + mob + dhw + heat + elyse0
        s_in, o_ww, o_h = df["storage_in_gwh"], df["storage_out_dhw_gwh"], df["storage_out_heat_gwh"]
        _seg(fig, x, o_h, top + s_in + o_ww, "Speicher OUT (Heiz) – kein Netzbezug", "Speicher OUT (Heiz)",
             0.7, COLORS["storage_out_heat"])
        _seg(fig, x, o_ww, top + s_in, "Speicher OUT (WW) – kein Netzbezug", "Speicher OUT (WW)",
             0.7, COLORS["storage_out_dhw"])
        _seg(fig, x, s_in, top, "Speicher IN", "Speicher IN", 0.85, COLORS["storage_in"])

    # Last als echte Segmente
    elyse = df["electrolysis_gwh"] if "electrolysis_gwh" in df else base * 0
    if elyse.sum() > 0:
        _seg(fig, x, elyse, base + mob + dhw + heat, "Elektrolyse", "Elektrolyse", 0.85, COLORS["electrolysis"])
    _seg(fig, x, heat, base + mob + dhw, "Heizung", "Heizung", 0.85, COLORS["heating"])
    _seg(fig, x, dhw, base + mob, "Warmwasser", "Warmwasser", 0.85, COLORS["dhw"])
    _seg(fig, x, mob, base, "Mobilität", "Mobilität", 0.85, COLORS["mobility"])
    _seg(fig, x, base, 0, "Basis-Last", "Basis-Last", 0.9, COLORS["base"])

    # Erzeugung als echte Segmente (halbtransparent schraffiert, über der Last)
    _seg(fig, x, sol, other + off + on, "Solar", "Solar", marker=_hatched("solar"))
    _seg(fig, x, on, other + off, "Wind Onshore", "Wind Onshore", marker=_hatched("wind_onshore"))
    _seg(fig, x, off, other, "Wind Offshore", "Wind Offshore", marker=_hatched("wind_offshore"))
    if other.abs().sum() > 0:
        _seg(fig, x, other, 0, "Sonstige EE", "Sonstige EE", marker=_hatched("other_ee"))

    fig.update_layout(
        barmode="overlay",
        font=dict(size=FONT_BASE),
        separators=GERMAN_SEPARATORS,
        title=dict(text=f"{title}<br><span style='font-size:15px'>{subtitle}</span>",
                   font=dict(size=20), x=0.5, xanchor="center"),
        xaxis=_axis(tickformat="%d.%m.",
                    # halber Tag Rand, damit die erste und letzte Säule vollständig sichtbar sind
                    range=[pd.Timestamp(start) - pd.Timedelta(hours=12), pd.Timestamp(end) + pd.Timedelta(hours=12)]),
        yaxis=_axis(title="Energie (GWh)"),
        legend=dict(orientation="h", yanchor="top", y=-0.07, xanchor="center", x=0.5, font=dict(size=FONT_LEGEND)),
        hoverlabel=dict(font=dict(size=FONT_LEGEND)),
        height=800, hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=70, r=30, t=110, b=170), bargap=0, bargroupgap=0,
        annotations=[dict(
            text="Hinweis: Legende blendet einzelne Segmente aus (Lücke statt Neustapelung).<br>"
                 "(c) Dr. Andreas Horn, www.solardoktor.de  |  Daten: Energy-Charts.info, Open-Meteo  |  Stand: "
                 + datetime.now().strftime("%d.%m.%Y"),
            xref="paper", yref="paper", x=0.5, y=-0.24, showarrow=False,
            font=dict(size=FONT_NOTE, color="#333333"), xanchor="center")],
    )
    return fig


def build_duration_curve(current: dict, previous: dict | None = None,
                         without_storage: np.ndarray | None = None) -> go.Figure:
    """Geordnete Jahresdauerlinie (Last − EE, Ø Tagesleistung in GW).

    ``current``/``previous``: {"curve": np.ndarray absteigend, "label": str}
    """
    fig = go.Figure()
    if previous is not None:
        prev = previous["curve"]
        fig.add_trace(go.Scatter(
            x=list(range(1, len(prev) + 1)), y=prev, mode="lines", name="Vorher: " + previous["label"],
            line=dict(color="#888888", width=2, dash="dash"),
            hovertemplate="Vorher, Tag %{x}: %{y:.1f} GW<extra></extra>",
        ))
    if without_storage is not None:
        fig.add_trace(go.Scatter(
            x=list(range(1, len(without_storage) + 1)), y=without_storage, mode="lines",
            name="Aktuell ohne Speicher (Referenz)", line=dict(color="#1f3b73", width=1.5, dash="dot"),
            hovertemplate="Ohne Speicher, Tag %{x}: %{y:.1f} GW<extra></extra>",
        ))
    cur = current["curve"]
    fig.add_trace(go.Scatter(
        x=list(range(1, len(cur) + 1)), y=cur, mode="lines", name="Aktuell: " + current["label"],
        line=dict(color="#1f3b73", width=2.5), fill="tozeroy", fillcolor="rgba(31,59,115,0.12)",
        hovertemplate="Aktuell, Tag %{x}: %{y:.1f} GW<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color="#333333", width=1))

    # Zweite x-Achse oben in Stunden (Tag d ↔ Stunde 24·d); unsichtbare Hilfsspur, damit Plotly sie zeichnet
    n = max(len(cur), len(previous["curve"]) if previous else 0)
    fig.add_trace(go.Scatter(x=[24, 24 * n], y=[0, 0], xaxis="x2", mode="markers", marker=dict(opacity=0),
                             showlegend=False, hoverinfo="skip"))
    fig.update_layout(
        font=dict(size=FONT_BASE),
        separators=GERMAN_SEPARATORS,
        xaxis=_axis(title="Tage [d] bzw. Stunden [h] (absteigend sortiert)", range=[1, n]),
        xaxis2=dict(title=dict(text="Stunden [h]", font=dict(size=FONT_AXIS_TITLE)), tickfont=dict(size=FONT_TICK),
                    overlaying="x", side="top", range=[24, 24 * n], showgrid=False, tickformat=",d"),
        yaxis=_axis(title="Superpower (−) bzw. Residuallast (+)<br>[GW, Ø Tagesleistung]", zeroline=False),
        legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="left", x=0, font=dict(size=FONT_LEGEND)),
        hoverlabel=dict(font=dict(size=FONT_LEGEND)),
        height=620, hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=80, r=30, t=80, b=170),
    )
    return fig


def build_temperature_storage(df: pd.DataFrame, start: date, end: date, design_temp: float | None) -> go.Figure:
    """Tagesmitteltemperatur (oben) und – bei aktivem Speicher – Speicher IN/OUT und Speicherinhalt (unten)."""
    has_storage = "storage_in_gwh" in df
    rows = 2 if has_storage else 1
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        row_heights=[0.4, 0.6] if has_storage else [1.0],
                        specs=[[{}], [{"secondary_y": True}]] if has_storage else [[{}]])
    x = df["date"]
    fig.add_trace(go.Scatter(x=x, y=df["temp_mean"], mode="lines", name="Tagesmitteltemperatur",
                             line=dict(color=COLORS["temperature"], width=1.5),
                             hovertemplate="Temperatur: %{y:.1f} °C<extra></extra>"), row=1, col=1)
    fig.add_hline(y=15, line=dict(color="#d32f2f", width=1, dash="dash"), row=1, col=1,
                  annotation_text="Heizgrenze 15 °C", annotation_position="top left",
                  annotation_font_size=FONT_NOTE)
    if design_temp is not None:
        fig.add_hline(y=design_temp, line=dict(color="#1565C0", width=1, dash="dash"), row=1, col=1,
                      annotation_text=f"Auslegungstemperatur {design_temp:.0f} °C".replace(".", ","),
                      annotation_position="bottom left", annotation_font_size=FONT_NOTE)
    if has_storage:
        fig.add_trace(go.Bar(x=x, y=df["storage_in_gwh"], name="Speicher IN", marker_color=COLORS["storage_in"],
                             hovertemplate="Speicher IN: %{y:.1f} GWh<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Bar(x=x, y=-df["storage_out_dhw_gwh"], name="Speicher OUT (WW)",
                             marker_color=COLORS["storage_out_dhw"], customdata=df["storage_out_dhw_gwh"],
                             hovertemplate="Speicher OUT (WW): %{customdata:.1f} GWh<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Bar(x=x, y=-df["storage_out_heat_gwh"], name="Speicher OUT (Heiz)",
                             marker_color=COLORS["storage_out_heat"], customdata=df["storage_out_heat_gwh"],
                             hovertemplate="Speicher OUT (Heiz): %{customdata:.1f} GWh<extra></extra>"), row=2, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["storage_soc_gwh"] / 1000.0, mode="lines", name="Speicherinhalt",
                                 line=dict(color="#6D4C41", width=2),
                                 hovertemplate="Speicherinhalt: %{y:.2f} TWh<extra></extra>"),
                      row=2, col=1, secondary_y=True)
        fig.update_yaxes(title_text="Speicher IN (+) / OUT (−) [GWh]", title_font=dict(size=FONT_AXIS_TITLE),
                         tickfont=dict(size=FONT_TICK), row=2, col=1, secondary_y=False,
                         gridcolor="rgba(200,200,200,0.25)")
        fig.update_yaxes(title_text="Speicherinhalt [TWh]", title_font=dict(size=FONT_AXIS_TITLE),
                         tickfont=dict(size=FONT_TICK), row=2, col=1, secondary_y=True, showgrid=False,
                         rangemode="tozero")
    fig.update_yaxes(title_text="Temperatur [°C]", title_font=dict(size=FONT_AXIS_TITLE),
                     tickfont=dict(size=FONT_TICK), row=1, col=1, gridcolor="rgba(200,200,200,0.25)")
    xr = [pd.Timestamp(start) - pd.Timedelta(hours=12), pd.Timestamp(end) + pd.Timedelta(hours=12)]
    fig.update_xaxes(range=xr, tickformat="%d.%m.", tickfont=dict(size=FONT_TICK), showgrid=True,
                     gridcolor="rgba(200,200,200,0.25)")
    fig.update_layout(
        barmode="relative", bargap=0, font=dict(size=FONT_BASE), separators=GERMAN_SEPARATORS,
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5, font=dict(size=FONT_LEGEND)),
        hovermode="x unified", hoverlabel=dict(font=dict(size=FONT_LEGEND)),
        height=620 if has_storage else 320, plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=80, r=80, t=30, b=90),
    )
    return fig
