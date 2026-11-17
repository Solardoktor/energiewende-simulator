"""Plotly-Grafik der Tagesbilanz.

Jede Kategorie ist ein echtes Segment (``base`` = Summe der darunterliegenden
Kategorien, ``y`` = eigener Wert). Wird eine Kategorie über die Legende
ausgeblendet, entsteht eine Lücke – die übrigen Segmente behalten Lage und Farbe.
Erzeugung (halbtransparent, schraffiert) liegt bewusst über der Last, sodass sich
die Farben mischen.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go

COLORS = {
    "superpower": "#FFF176",
    "base": "#808080",
    "mobility": "#FF99FF",
    "dhw": "#C2185B",
    "heating": "#d32f2f",
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
    "other_ee": ("#1976D2", "#1976D2"),
}

def _seg(fig, x, y, base, name, hover_label, opacity=1.0, color=None, marker=None):
    fig.add_trace(go.Bar(
        x=x, y=y, base=base, name=name, opacity=opacity,
        marker=marker or dict(color=color),
        hovertemplate=f"{hover_label}: %{{y:.1f}} GWh<extra></extra>",
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
            solidity=0.6,
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
        customdata=df["ee_minus_load"],
        hovertemplate="EE − Last: %{customdata:.1f} GWh<extra></extra>",
    ))

    # Last als echte Segmente
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
        title=dict(text=f"{title}<br><span style='font-size:13px'>{subtitle}</span>",
                   font=dict(size=18), x=0.5, xanchor="center"),
        xaxis=dict(title="", tickformat="%d.%m.", showgrid=True, gridcolor="rgba(200,200,200,0.25)",
                   # halber Tag Rand, damit die erste und letzte Säule vollständig sichtbar sind
                   range=[pd.Timestamp(start) - pd.Timedelta(hours=12), pd.Timestamp(end) + pd.Timedelta(hours=12)]),
        yaxis=dict(title="Energie (GWh)", showgrid=True, gridcolor="rgba(200,200,200,0.25)"),
        legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="center", x=0.5, font=dict(size=10)),
        height=760, hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=30, t=100, b=140), bargap=0, bargroupgap=0,
        annotations=[dict(
            text="Hinweis: Legende blendet einzelne Segmente aus (Lücke statt Neustapelung).<br>"
                 "(c) Dr. Andreas Horn, www.solardoktor.de  |  Daten: Energy-Charts.info, Open-Meteo  |  Stand: "
                 + datetime.now().strftime("%d.%m.%Y"),
            xref="paper", yref="paper", x=0.5, y=-0.20, showarrow=False,
            font=dict(size=11, color="#333333"), xanchor="center")],
    )
    return fig


def build_duration_curve(current: dict, previous: dict | None = None) -> go.Figure:
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
    cur = current["curve"]
    fig.add_trace(go.Scatter(
        x=list(range(1, len(cur) + 1)), y=cur, mode="lines", name="Aktuell: " + current["label"],
        line=dict(color="#1f3b73", width=2.5), fill="tozeroy", fillcolor="rgba(31,59,115,0.12)",
        hovertemplate="Aktuell, Tag %{x}: %{y:.1f} GW<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color="#333333", width=1))
    fig.update_layout(
        xaxis=dict(title="Tage (absteigend sortiert)", showgrid=True, gridcolor="rgba(200,200,200,0.25)",
                   range=[1, max(len(cur), len(previous["curve"]) if previous else 0)]),
        yaxis=dict(title="Residuallast (+) / Superpower (−) [GW, Ø Tagesleistung]", showgrid=True,
                   gridcolor="rgba(200,200,200,0.25)", zeroline=False),
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0, font=dict(size=11)),
        height=560, hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=30, t=30, b=150),
    )
    return fig
