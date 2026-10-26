"""Persistente Datenablage (Parquet + JSON-Metadaten) mit Aktualisierungsregeln.

Zwei Speicherorte werden gelesen; der jeweils jüngere Abruf gewinnt:
  * ``data/``  – im Repository versioniert (per GitHub Action oder Skript aktualisiert)
  * ``cache/`` – zur Laufzeit beschreibbar (auf Streamlit Cloud flüchtig)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import config


@dataclass
class Entry:
    df: pd.DataFrame
    fetched_at: datetime
    complete_until: date | None
    source: str                      # "data/", "cache/" oder "API"
    extra: dict = field(default_factory=dict)

    def age_days(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.fetched_at).total_seconds() / 86400


class Store:
    def __init__(self, read_dirs: list[Path] | None = None, write_dir: Path | None = None):
        self.read_dirs = read_dirs or [config.BUNDLED_DATA_DIR, config.RUNTIME_CACHE_DIR]
        self.write_dir = write_dir or config.RUNTIME_CACHE_DIR

    def load(self, key: str) -> Entry | None:
        best = None
        for d in self.read_dirs:
            pq, meta = d / f"{key}.parquet", d / f"{key}.json"
            if not (pq.exists() and meta.exists()):
                continue
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                fetched = datetime.fromisoformat(m["fetched_at"])
                if best is not None and fetched < best.fetched_at:   # Gleichstand: spätere Ablage (cache/) gewinnt
                    continue
                cu = date.fromisoformat(m["complete_until"]) if m.get("complete_until") else None
                best = Entry(pd.read_parquet(pq), fetched, cu, f"{d.name}/",
                             {k: v for k, v in m.items() if k not in ("fetched_at", "complete_until")})
            except Exception:
                continue
        return best

    def save(self, key: str, df: pd.DataFrame, complete_until: date | None, **extra) -> Entry:
        fetched = datetime.now(timezone.utc).replace(microsecond=0)
        entry = Entry(df, fetched, complete_until, "API", extra)
        try:
            self.write_dir.mkdir(parents=True, exist_ok=True)
            df.to_parquet(self.write_dir / f"{key}.parquet", index=False)
            meta = {"fetched_at": fetched.isoformat(),
                    "complete_until": complete_until.isoformat() if complete_until else None, **extra}
            (self.write_dir / f"{key}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        except Exception:
            pass   # Schreibfehler sind nicht kritisch – Daten bleiben im Speicher nutzbar
        return entry


def max_age_days(year: int, today: date) -> float:
    """Maximales Alter eines Jahres-Datensatzes.

    Jahre vor dem letzten vollständigen Kalenderjahr: jährlich, jüngere: monatlich.
    """
    if year < today.year - 1:
        return config.MAX_AGE_OLD_YEARS_DAYS
    return config.MAX_AGE_RECENT_DAYS


def needs_refresh(entry: Entry | None, needed_until: date, max_age: float,
                  now: datetime | None = None) -> bool:
    """True, wenn der Datensatz fehlt, zu alt ist oder den benötigten Zeitraum nicht abdeckt.

    Fehlende Tage lösen frühestens nach ``MIN_RETRY_HOURS`` einen neuen Abruf aus,
    damit unvollständige API-Daten nicht bei jedem Seitenaufruf neu geladen werden.
    """
    if entry is None:
        return True
    now = now or datetime.now(timezone.utc)
    age = entry.age_days(now)
    if age > max_age:
        return True
    incomplete = entry.complete_until is None or entry.complete_until < needed_until
    return incomplete and age * 24 >= config.MIN_RETRY_HOURS


def year_needed_until(year: int, period_end: date) -> date:
    return min(date(year, 12, 31), period_end)


def last_day_of_previous_month(today: date) -> date:
    return today.replace(day=1) - timedelta(days=1)
