"""Aktualisiert die im Repository versionierten Daten unter ``data/``.

Regeln (siehe energiewende/config.py):
  * Jahre vor dem letzten vollständigen Kalenderjahr: jährlich neu abrufen
  * letztes vollständiges Jahr und laufendes Jahr (bis Ende Vormonat): monatlich
  * installierte Leistung: bei jedem Lauf; Temperaturen des Default-Standorts bei Bedarf

Aufruf:  python scripts/update_data.py            (nur fällige Datensätze)
         python scripts/update_data.py --force    (alles neu)
Wird monatlich von .github/workflows/update-data.yml ausgeführt.
"""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from energiewende import config, data  # noqa: E402
from energiewende.store import (Store, last_day_of_previous_month, max_age_days,  # noqa: E402
                                needs_refresh, year_needed_until)

PAUSE_S = 35   # Energy-Charts: 2 Anfragen/min je Endpoint


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--from-year", type=int, default=config.FIRST_YEAR)
    args = ap.parse_args()

    store = Store([config.BUNDLED_DATA_DIR], config.BUNDLED_DATA_DIR)
    today = date.today()
    period_end = last_day_of_previous_month(today)
    errors = 0

    for year in range(args.from_year, period_end.year + 1):
        needed = year_needed_until(year, period_end)
        entry = store.load(f"power_{year}")
        if not args.force and not needs_refresh(entry, needed, max_age_days(year, today)):
            print(f"power_{year}: aktuell (Abruf {entry.fetched_at:%Y-%m-%d})")
            continue
        entry, notes = data.load_power_year(store, year, needed, today, force=True)
        print(f"power_{year}: {'aktualisiert' if entry and entry.source == 'API' else 'FEHLER'} {' '.join(notes)}")
        errors += bool(notes)
        time.sleep(PAUSE_S)

    r = data.load_installed(today, store, force=True)
    print("installed_monthly:", "; ".join(r.notes) or "aktualisiert")
    errors += bool(r.notes)

    loc = config.DEFAULT_LOCATION
    r = data.load_temperatures(loc, date(args.from_year, 1, 1), period_end, today, store)
    print(f"Temperaturen {loc}:", "; ".join(r.notes) or "ok")
    ref, notes = data.load_hgt_reference(loc, store)
    print(f"Referenz-Heizgradtage {loc}: {ref:.0f}" if ref else f"Referenz-HGT: {notes}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
