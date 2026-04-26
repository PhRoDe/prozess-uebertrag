# Prozess-Übertrag

Interne Calandi-Web-App. Jahresabschluss- und BWA-PDFs per Drag-and-Drop hochladen,
Claude extrahiert den Kontennachweis der GuV, Output ist eine Excel die die
**Gliederung der Original-PDF 1:1 übernimmt** (nicht HGB-normalisiert).

## Wo es läuft

| Was | Wo |
|---|---|
| Live-App | https://prozess-uebertrag-production.up.railway.app |
| Code-Repo | https://github.com/PhRoDe/prozess-uebertrag (private) |
| Deploy | Railway, `railway up` aus dem Projektordner |
| Supabase | Projekt `prozess-uebertrag` (Frankfurt) |
| Team-Passwort | siehe `TEAM_CREDENTIALS.local.md` (gitignored) |

## Architektur in einem Satz

FastAPI-Monolith auf Railway, HTMX-UI mit Tailwind-CDN, Claude-API für PDF-Extraktion,
Supabase für Storage+Postgres, alles in einem Python-Container.

## Kern-Prinzip: PDF-Struktur + GKV-Anker

Jede Buchhaltungssoftware gliedert den Kontennachweis anders:

- DATEV-Standard: "1. Umsatzerlöse", "2. Sonstige betriebliche Erträge", ...
- Andere Vorlagen: "Raumkosten" und "Fahrzeugkosten" als eigene Hauptkategorien
  statt Sub-Gruppen unter "Sonst. betr. Aufwendungen"
- Manche mit Nummerierung (5.1-5.5), manche ohne

**Die Gruppen-Reihenfolge und -Bezeichnungen kommen aus der PDF.** Claude
extrahiert die Konten 1:1. Daneben klassifiziert Claude jede Gruppe per
`gkv_section`-Slug nach §275 HGB GKV (`umsatzerloese`, `materialaufwand_rhb`,
`personalaufwand_loehne`, `ee_steuern`, `gewinnvortrag`, ...). Diese
Klassifikation ist STB-unabhängig und dient als Anker für:

- Multi-Jahr-Cross-Matching (Gruppe in JA-2022 vs JA-2024 selbe Section → gleiche Excel-Zeile)
- JÜ-Formel-Vorzeichen (Aufwand-Sektionen werden subtrahiert, ertrag-Sektionen addiert)
- Routing der Bilanzgewinn-Positionen in einen separaten Block am Ende

## Plausibilitäts-Anker

Claude muss `pdf_jahresueberschuss_gj/_vj` als Pflichtfeld liefern. Der Builder
rendert nach der JÜ-Formel-Zeile:

```
Jahresergebnis            =Summe-Formel-aus-Gruppen
PDF-Jahresüberschuss      [Wert aus PDF]
Differenz Excel ↔ PDF     =JE-Zelle - PDF-Zelle
```

Build-Zeit-Cross-Check: numerische Excel-JÜ wird gegen PDF-JÜ verglichen, Diff
> 1 ct landet als `jue_excel_vs_pdf_mismatch` im Fragen-Sheet. Damit kein Excel
mehr silent falsch ist — der Anwender sieht nach jedem Upload ob der Übertrag
stimmt.

## Bilanzgewinn-Block

Gewinnvortrag, Verlustvortrag, Ausschüttung, Bilanzgewinn werden NICHT
ausgefiltert (das war der frühere Hack), sondern landen in einem eigenen Block
nach dem Jahresergebnis:

```
--- Bilanzgewinn-Rechnung ---
Gewinnvortrag             [Konten + Summe]
Ausschüttung              [Konten + Summe]
Bilanzgewinn (Formel)     =JE + Gewinnvortrag - Ausschüttung
```

Routing: per `gkv_section in {gewinnvortrag, ausschuettung, bilanzgewinn}` ODER
per Name-Match (Defense-in-Depth, falls Claude die Section vergisst).

## Kern-Dateien

| Datei | Zweck |
|---|---|
| `app/worker/prompts.py` | Extraktions-Prompts für Claude — Quelle der Wahrheit für "wie soll das JSON aussehen" |
| `app/worker/claude_client.py` | anthropic SDK Wrapper mit 429-Retry + `<pdf_content>`-Delimiter |
| `app/worker/consolidate.py` | Multi-Jahr-Merging, Spalten-Bau (JA+BWA getrennt), Vorjahres-Cross-Check |
| `app/excel/builder.py` | Dynamisches Layout, Summe-zuerst, sign-aware Jahresergebnis |
| `app/routes/pages.py` | Login, Home, Logout |
| `app/routes/upload.py` | PDF-Upload, Rate-Limit, Filename-Sanitize |
| `app/routes/job.py` | Status-Polling, Review-Screen, Finalize |
| `app/worker/tasks.py` | Background-Orchestrator, Idempotent + Claim-Pattern |
| `app/ratelimit.py` | In-Memory-Limiter, X-Forwarded-For-aware (hinter Railways Proxy) |

## Wichtige Regeln (nicht brechen)

- Extraktions-Logik lebt in `app/worker/`, **KEINE HTTP-Imports** dort.
- Excel-Logik lebt in `app/excel/`, **KEINE Netzwerk-Calls** dort.
- **Alle Excel-Zwischensummen MÜSSEN Formeln sein** (`=SUM(...)` oder Kaskaden).
  Niemals hardcoded Werte.
- Werte behalten **ihr Vorzeichen wie im PDF** (Claude normalisiert nicht).
  Die Jahresergebnis-Formel unterscheidet `expenses_negative` vs
  `expenses_positive` und addiert/subtrahiert entsprechend. `sign_convention`
  wird im Builder aus den Daten abgeleitet (Summe der Aufwand/Steuer-Werte),
  nicht blind aus Claude übernommen.
- **`gkv_section` ist authoritativ** über `type` für die JÜ-Formel-Klassifikation
  (siehe `SECTION_ROLE` in `app/excel/builder.py`). Wenn Claude den `type` driftet
  (z.B. Steuern als "neutral"), wirkt der Section-Slug als Korrektiv.
- **Sensitive Daten nie in stdout/Bash-Output**: `railway variables` zeigt alles
  Klartext — niemals das Output anzeigen. Nur Namen, nicht Werte.
- **Keine HGB-Normalisierung der Reihenfolge**: Wenn eine PDF "Raumkosten" als
  Hauptgruppe zeigt, bleibt das so. Aber gkv_section bringt die semantische
  Standardisierung dazu.
- **Excel-Zahlenformat**: `'#,##0.00;-#,##0.00'` — kein `[Red]`, neutrale
  Darstellung mit Minus-Zeichen.

## Typische Workflows

### Lokal testen
```bash
cd "/Users/philippdegen/Documents/Claude/Calandi/Prozess-Übertrag"
.venv/bin/uvicorn app.main:app --reload
# → http://localhost:8000, Passwort aus TEAM_CREDENTIALS.local.md
```

### Live-Smoketest gegen Claude API
```bash
# synthetisches Mini-PDF, prüft Pipeline ohne reale Daten
.venv/bin/python3 tests/fixtures/smoketest_claude.py

# End-to-End mit echten JA-PDFs (1+ Pfade), wirft Excel raus + Cross-Check
.venv/bin/python3 tests/fixtures/smoketest_e2e.py "<pfad/ja1.pdf>" "<pfad/ja2.pdf>" ...
# Ergebnis-Excel: smoketest_output.xlsx im Projekt-Root
# Achten auf: Fragen-Sheet (sollte leer sein) + Excel-JÜ ↔ PDF-JÜ-Diff
```

### Deploy
```bash
# Unit-Tests grün?
.venv/bin/pytest
# GitHub-Backup
git push
# Railway-Deploy
railway up
```

### Prompt anpassen
1. `app/worker/prompts.py` ändern
2. `.venv/bin/python3 tests/fixtures/smoketest_claude.py` — verifizieren dass
   Claude noch valides JSON liefert
3. Test gegen ein echtes Jahresabschluss-PDF aus `M&A/` — Excel manuell öffnen
   und mit der PDF querprüfen
4. Commit + Deploy

## Bekannte Grenzen und Follow-ups

- **Supabase-Service-Key wurde nicht rotiert** (siehe Chat-Historie vom
  2026-04-24 — Railway-Variables-Leak). **Vor dem ersten echten Deal-Upload
  rotieren.**
- **Rate-Limit ist In-Memory per Container** — bei Railway-Multi-Replica
  funktioniert das nicht mehr. Aktuell OK weil single-replica.
- **Review-Screen** triggered nur wenn Claude echte `open_questions` liefert.
  Nach dem Umbau (PDF-Gliederung 1:1) passiert das selten. Wenn ein
  exotisches PDF reinkommt das Claude nicht einordnet → Review-UI zeigt alle
  Gruppen aus der konsolidierten Struktur als Dropdown.
- **Scan-PDFs** dauern 2-4 min und kosten ~0,40-0,60 €/PDF (Claude Vision).
  Nicht blockieren bei großen Scan-Deals, aber User warnen.
- **STB-Vorzeichen-Inversionen in VJ-Spalten** (Beispiel: Gewürze-PDFs JA-2023+
  JA-2024 drucken den VJ-JÜ und VJ-Konten mit umgekehrtem Vorzeichen). Der
  Plausibilitäts-Anker macht das im Fragen-Sheet sichtbar
  (`pdf_jue_previous_year_mismatch`, `previous_year_mismatch`), aber wir
  flippen nicht automatisch — die Inversionen sind nicht trivial vorhersagbar
  (manche Konten ja, manche nein) und Auto-Flip riskiert silent falsche Werte.
  Empfohlener Workflow bei Mismatch: betroffene Spalte manuell mit der PDF
  abgleichen, ggf. den Eigenjahres-PDF-Wert als authoritativ nehmen.
- **Phase 3 (festes GKV-Layout in §275-Reihenfolge)**: nicht umgesetzt, weil
  bei DATEV-Standard ohnehin die PDF-Reihenfolge der GKV-Reihenfolge
  entspricht. Bei exotischen STBs könnte ein erzwungenes GKV-Layout später
  helfen. Tracking via gkv_section ist schon drin, würde nur den Builder
  erweitern.

## Test-Suite

```bash
.venv/bin/pytest                      # ~66 Tests
.venv/bin/pytest tests/test_xxx.py   # einzelnes Modul
```

- Unit-Tests decken alle `app/*`-Module
- Keine E2E-Tests gegen Live-Claude (zu teuer, zu langsam) — stattdessen
  `tests/fixtures/smoketest_claude.py` als manueller Smoketest

## Dokumentation

- `docs/specs/2026-04-23-prozess-uebertrag-design.md` — Original-Design
- `docs/plans/2026-04-23-implementation-plan.md` — ursprünglicher Plan (historisch)
- `docs/exploration/htmx-demo.html` — UI-Prototyp vor Implementierung
- `README.md` — Deploy-Anleitung für neue Team-Mitglieder
- Wiki-Eintrag (im Eltern-Repo): `wiki/projekte/prozess-uebertrag.md`

## Sprache

- Code-Kommentare Englisch
- User-facing Strings (Templates, Error-Messages) Deutsch
- Tests Deutsch wo es um Domain-Begriffe geht, Englisch bei generischen
  Behavior-Tests
