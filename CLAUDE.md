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

## Kern-Prinzip: PDF ist die Wahrheit

**Die Gruppenstruktur der Excel kommt aus der PDF, nicht aus dem HGB.** Jede
Buchhaltungssoftware gliedert den Kontennachweis anders:

- DATEV-Standard: "1. Umsatzerlöse", "2. Sonstige betriebliche Erträge", ...
- Andere Vorlagen: "Raumkosten" und "Fahrzeugkosten" als eigene Hauptkategorien
  statt Sub-Gruppen unter "Sonst. betr. Aufwendungen"
- Manche mit Nummerierung (5.1-5.5), manche ohne

Claude übernimmt die Struktur wie sie im PDF steht. Der Builder rendert das
dynamisch. Es gibt **keine feste GUV_HIERARCHY mehr** im Code.

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
  `expenses_positive` und addiert/subtrahiert entsprechend.
- **Sensitive Daten nie in stdout/Bash-Output**: `railway variables` zeigt alles
  Klartext — niemals das Output anzeigen. Nur Namen, nicht Werte.
- **Keine HGB-Normalisierung**: Wenn eine PDF "Raumkosten" als Hauptgruppe zeigt,
  bleibt das so. Nicht in ein festes Schema zwingen.

## Typische Workflows

### Lokal testen
```bash
cd "/Users/philippdegen/Documents/Claude/Calandi/Prozess-Übertrag"
.venv/bin/uvicorn app.main:app --reload
# → http://localhost:8000, Passwort aus TEAM_CREDENTIALS.local.md
```

### Live-Smoketest gegen Claude API
```bash
.venv/bin/python3 tests/fixtures/smoketest_claude.py
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
