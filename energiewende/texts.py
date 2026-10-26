"""Texte der Oberfläche (Erläuterungen, Definitionen, Dokumentation)."""

from . import config

INTRO = """
**Skalierte Tagesenergie-Übersicht für Deutschland**  
Datenbasis: Energy-Charts.info (Fraunhofer ISE) – Gesamte Nettostromerzeugung.  
Annahme: Intra-Day-Abweichungen zwischen Last und Erzeugung können vollständig durch Kurzzeitspeicher und Flexibilitäten ausgeglichen werden.  
Weitere Infos, Erläuterungen und Ergebnisse sowie Platz für Ihren Kommentar finden Sie unter [solardoktor.de/energiewende-ueberblick](https://solardoktor.de/energiewende-ueberblick/).
"""

DUNKELFLAUTE_DEFINITION = """
**Dunkelflaute:** Ein Zeitraum aus einem oder mehreren aufeinanderfolgenden Tagen, an denen die
erneuerbare Stromerzeugung (Wind, Sonne und sonstige Erneuerbare) den Tagesverbrauch nicht deckt.
Das Defizit lässt sich nicht durch Kurzzeitspeicher und kurzfristige Flexibilität innerhalb von
24 Stunden ausgleichen, sondern muss aus Langzeitspeichern, Importen oder steuerbaren Kraftwerken
gedeckt werden.  
*Im Modell:* jeder Tag, dessen EE-Tagesenergie unter dem Tagesverbrauch liegt (Tagesenergiebilanz).
Der Zeitraum wird als stationäres Jahr betrachtet: Ende und Anfang sind verknüpft, sodass eine
Dunkelflaute über die Zeitraumgrenze laufen kann (gekennzeichnet mit ↻).  
→ [Definition auf solardoktor.de](https://solardoktor.de/definition-dunkelflaute/)
"""

EXPLANATION = """
**Annahme zur Intra-Day-Ausgleichbarkeit**  
Die Tages-Energiewerte geben einen Überblick zur Energiewende unter der Annahme, dass Intra-Day-Abweichungen zwischen Last und Erzeugung vollständig durch Kurzzeitspeicher und Flexibilitäten (Intra-Day-Batteriespeicher, Lastverschiebung beim Laden von Elektrofahrzeugen, V2G, Wärmepufferspeicher etc.) ausgeglichen werden können.

Dies ist Stand heute noch nicht in vollem Umfang möglich, aber mittelfristig aufgrund des massiven Ausbaus von Speichern und Flexibilitätsoptionen machbar.

**Datenbasis**  
- Gesamte Nettostromerzeugung (total_power) von Energy-Charts.info / Fraunhofer ISE, aggregiert je Kalendertag in deutscher Ortszeit  
- Skalierung der Solar- und Windstromerzeugung anhand der installierten Leistung (Monatswerte, tageweise interpoliert)  
- Zusatzlasten für Sektorenkopplung (Mobilität, Warmwasser, Heizung) mit saisonalen und temperaturabhängigen Mustern  
- Temperaturdaten: Open-Meteo (Default: München; anderer Standort in der Seitenleiste wählbar)

**Lizenz**  
Daten: CC BY 4.0, Attribution: energy-charts.info
"""

DEBUG_COLUMNS = """
**Spalten-Erklärung**
- `ee_day`: Summe der (skalierten) EE (Solar + Wind Onshore + Wind Offshore + Sonstige EE)
- `total_load_gwh`: Gesamtlast (Basis-Last + Zusatzlasten)
- `ee_minus_load` = `ee_day` − `total_load_gwh`
- `superpower_day` = max(0, ee_minus_load) → positiver Überschuss
- `residual_day` = max(0, −ee_minus_load) → fehlende Energie
- `coverage`: Anteil der Stunden des Tages mit Energy-Charts-Daten (1,0 = vollständig)
- `installed_*`: installierte Leistung (GW, interpoliert), `scale_*`: Skalierungsfaktor Ziel/Ist
"""


def assumptions_markdown() -> str:
    c = config
    drive = ", ".join(f"{m}: {c.DRIVING_MONTH_FACTORS[m]:.2f}".replace(".", ",") for m in range(1, 13))
    return f"""
### Annahmen des Modells

**Mobilität**
- Elektro-PKW: 12 500 km/Jahr × 18 kWh/100 km ≈ 2 250 kWh/Fahrzeug/Jahr → 40 Mio. PKW ≈ 90 TWh
- Elektro-LKW: 80 000 km/Jahr × 50 kWh/100 km ≈ 40 000 kWh/Fahrzeug/Jahr → 4 Mio. LKW ≈ 160 TWh
- **Saisonales Muster** = Fahrleistung × Verbrauch je km, anschließend auf die Jahresmenge normiert:
  - *Fahrleistung* im Sommer höher (Urlaubs- und Freizeitverkehr). Stilisierte Monatsfaktoren
    (Monat: Faktor) {drive}. Die Größenordnung (± 5–6 %) orientiert sich an monatlichen
    Inlandsablieferungen von Ottokraftstoff (BAFA/UFOP); Ablieferungen sind nur ein Näherungswert
    für die Fahrleistung (Lagerhaltung, Preiseffekte).
  - *Verbrauch je km* temperaturabhängig: +1,2 % je Kelvin unter 15 °C (Heizung, Batterie),
    +0,5 % je Kelvin über 22 °C (Klimatisierung). Bei 0 °C ≈ +18 %, bei −5 °C ≈ +24 %.
  - Beide Effekte gleichen sich teilweise aus; netto liegt der Ladebedarf im Winter leicht höher.
- **Wochenend-/Feiertagsanteil**: Der eingestellte Anteil der Ladeenergie wird gezielt auf
  Wochenenden und bundesweite Feiertage gelegt, der Rest folgt dem saisonalen Profil über alle Tage.
  Die Jahresmenge bleibt erhalten.

**Warmwasser**
- 2 000 kWh pro Einwohner und Jahr → 40 Mio. EW ≈ 80 TWh; Winter (Nov.–März) ×1,10, sonst ×0,95, normiert

**Heizung**
- Wärmebedarf 10 000 kWh pro Einwohner im *Normaljahr* (= Strombedarf bei Direktheizung) → 40 Mio. EW ≈ 400 TWh
- Tagesverteilung proportional zu den Heizgradtagen (Basis 15 °C) am gewählten Standort,
  kalibriert auf die mittleren Heizgradtage der Klimanormalperiode {c.HGT_REFERENCE_YEARS[0]}–{c.HGT_REFERENCE_YEARS[1]}:
  kalte Jahre ergeben mehr, warme weniger Heizenergie
- Ohne Temperaturdaten: Monatsprofil, auf den Jahreswert normiert
- COP Luft-Wasser: 2,2 + 0,08 × T (begrenzt 1,8–4,5); COP Wasser-Wasser/Sole: 4,0 + 0,03 × T (begrenzt 3,5–5,5)
- Mit den Default-Anteilen (60 % Luft-WP, 10 % Sole-WP, 30 % direkt) beträgt der Strombedarf etwa die Hälfte
  des Wärmebedarfs, mit 100 % Wärmepumpen etwa 35–45 % (COP im Winter niedriger)

**Erzeugung**
- Tagesaggregation in deutscher Ortszeit; Intervalllänge aus den Zeitstempeln (15 oder 60 min)
- Skalierung Solar/Wind auf die Zielleistung (abschaltbar); installierte Leistung gilt laut
  Energy-Charts zum Monatsende und wird tageweise linear interpoliert
- „Status Quo“ = installierte Leistung am Ende des gewählten Zeitraums
- Sonstige EE (Biomasse, Laufwasser, Speicherwasser, Geothermie, 50 % Abfall) unskaliert
- Pumpspeicher optional (netto = Erzeugung minus Pumpstrom, d. h. die Speicherverluste)

**CO₂**
- Residual-Intensität (Default 1 000 g/kWh) tagesweise nach Verbrauch auf die Kategorien umgelegt;
  der Mittelwert über alle Kategorien entspricht Residualenergie / Verbrauch × Intensität

**Datenaktualität**
- Abgeschlossene Jahre liegen als Dateien im Repository (`data/`) und werden automatisch erneuert:
  Jahre vor dem letzten vollständigen Kalenderjahr jährlich, jüngere Daten monatlich
  (Energy-Charts korrigiert Werte gelegentlich rückwirkend)
"""


DONATION = """
Wenn dir das Tool weiterhilft, freue ich mich über eine Unterstützung:

**Wero**  
[https://share.wero-wallet.eu/p/1/c/xJoLOYMLRK](https://share.wero-wallet.eu/p/1/c/xJoLOYMLRK)

**PayPal**  
[https://www.paypal.com/donate?hosted_button_id=33BEDTQ877SA4](https://www.paypal.com/donate?hosted_button_id=33BEDTQ877SA4)

Kontakt: [solardoktor.de](https://www.solardoktor.de)
"""
