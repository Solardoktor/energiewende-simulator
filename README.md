# Energiewende-Simulator (Solar Doktor)

Streamlit-App für eine skalierte Tagesenergie-Übersicht Deutschlands auf Basis von
Energy-Charts.info (Fraunhofer ISE) und Open-Meteo.

## Struktur

| Pfad | Inhalt |
|---|---|
| `SDR_TOOL_Energiewende-Simulator.py` | Streamlit-Oberfläche (Hauptdatei) |
| `energiewende/config.py` | Annahmen, Szenarien, Serien-IDs, Aktualisierungsregeln |
| `energiewende/data.py` | API-Abruf, Tagesaggregation (Ortszeit), installierte Leistung, Temperaturen |
| `energiewende/store.py` | Ablage (`data/` versioniert, `cache/` zur Laufzeit) und Aktualisierungsregeln |
| `energiewende/model.py` | Rechenmodell, Bilanz, Dunkelflauten (stationäres Jahr) |
| `energiewende/charts.py` | Plotly-Grafik |
| `energiewende/texts.py` | Erläuterungen und Dokumentation |
| `scripts/update_data.py` | aktualisiert `data/` (monatlich per GitHub Action) |
| `tests/` | pytest-Tests (ohne Netzzugriff) |

## Lokal starten

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
streamlit run SDR_TOOL_Energiewende-Simulator.py
```

## Daten und Aktualität

Abgeschlossene Jahre liegen als Parquet-Dateien unter `data/`. Die Action
`.github/workflows/update-data.yml` läuft monatlich und erneuert Jahre vor dem letzten
vollständigen Kalenderjahr jährlich, jüngere Daten monatlich. Die App lädt fehlende
oder veraltete Daten zusätzlich selbst nach (Zwischenspeicher `cache/`).
Erstbefüllung: Action im Reiter *Actions* manuell starten (`Run workflow`) oder lokal
`python scripts/update_data.py` ausführen und `data/` committen.

## Streamlit Community Cloud

- Main file path: `SDR_TOOL_Energiewende-Simulator.py`
- Advanced settings → Python 3.12
- Abhängigkeiten sind in `requirements.txt` exakt gepinnt; Updates bewusst lokal testen.
