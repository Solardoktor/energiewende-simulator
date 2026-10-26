"""Diagnose der Datenquellen – zeigt Dauer, Status, Größe und Aufbau jeder Antwort.

Aufruf (im Ordner, in dem SDR_TOOL_Energiewende-Simulator.py liegt):
    python scripts/diagnose_api.py           # schnelle Tests (1 Tag, 1 Monat, installierte Leistung, Temperatur)
    python scripts/diagnose_api.py --full    # zusätzlich ein ganzes Jahr total_power (dauert länger)

Energy-Charts erlaubt ca. 2 Abrufe/Minute je Endpoint (kurzer Burst bis 4).
Das Skript bleibt darunter. Die Ausgabe bitte vollständig kopieren.
"""

import argparse
import json
import sys
import time
from datetime import date, timedelta

import requests

EC = "https://api.energy-charts.info/v2"
OM = "https://archive-api.open-meteo.com/v1/archive"
UA = {"User-Agent": "SDR-Energiewende-Simulator-Diagnose"}


def probe(name, url, params, timeout=120):
    print(f"\n=== {name}\nGET {url} {params}")
    t0 = time.perf_counter()
    try:
        r = requests.get(url, params=params, timeout=(10, timeout), headers=UA)
    except Exception as e:  # noqa: BLE001
        print(f"FEHLER nach {time.perf_counter() - t0:.1f} s: {type(e).__name__}: {e}")
        return None
    dt = time.perf_counter() - t0
    hdr = {k: v for k, v in r.headers.items() if k.lower() in
           ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "content-type", "content-encoding")}
    print(f"HTTP {r.status_code} in {dt:.1f} s, {len(r.content) / 1e6:.2f} MB, Header: {hdr}")
    if r.status_code != 200:
        print("Antwort:", r.text[:500])
        return None
    try:
        js = r.json()
    except ValueError:
        print("Keine JSON-Antwort:", r.text[:300])
        return None
    if isinstance(js, dict):
        print("Schlüssel:", list(js.keys()))
        meta = {k: v for k, v in js.items() if k not in ("data", "daily")}
        print("Metadaten:", json.dumps(meta, ensure_ascii=False, default=str)[:800])
        recs = js.get("data")
        if isinstance(recs, list) and recs:
            print(f"Datensätze: {len(recs)}")
            print("erster:", json.dumps(recs[0], ensure_ascii=False)[:600])
            print("letzter Zeitstempel:", recs[-1].get("timestamp"))
            series = sorted({k for rec in recs[:50] for k in (rec.get("values") or {})})
            print("Serien:", ", ".join(series))
    return js


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="auch ein ganzes Jahr total_power abrufen")
    args = ap.parse_args()
    print("Python", sys.version.split()[0], "| requests", requests.__version__)
    y = date.today().year - 1

    probe("total_power, 1 Tag", f"{EC}/total_power", {"country": "de", "start": f"{y}-03-30", "end": f"{y}-03-31"})
    probe("total_power, 1 Monat", f"{EC}/total_power", {"country": "de", "start": f"{y}-01-01", "end": f"{y}-02-01"})
    probe("installed_power, monatlich", f"{EC}/installed_power", {"country": "de", "time_step": "monthly"})
    probe("Open-Meteo, 1 Jahr München", OM, {"latitude": 48.1351, "longitude": 11.582, "start_date": f"{y}-01-01",
          "end_date": f"{y}-12-31", "daily": "temperature_2m_mean", "timezone": "Europe/Berlin"})
    probe("Open-Meteo, 30 Jahre München (Referenz-Heizgradtage)", OM, {"latitude": 48.1351, "longitude": 11.582,
          "start_date": "1991-01-01", "end_date": "2020-12-31", "daily": "temperature_2m_mean",
          "timezone": "Europe/Berlin"})
    if args.full:
        print("\nWarte 65 s (Rate-Limit Energy-Charts) …")
        time.sleep(65)
        start = date(y, 1, 1) - timedelta(days=1)
        probe("total_power, ganzes Jahr", f"{EC}/total_power",
              {"country": "de", "start": start.isoformat(), "end": f"{y + 1}-01-01"}, timeout=300)


if __name__ == "__main__":
    main()
