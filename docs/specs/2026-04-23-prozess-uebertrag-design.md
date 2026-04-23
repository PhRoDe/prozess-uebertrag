---
title: Prozess-Übertrag — Web-App für GuV-Extraktion
type: spec
status: draft
created: 2026-04-23
updated: 2026-04-23
owner: Philipp Degen
related:
  - /skills/guv-uebertrag/SKILL.md
  - ./docs/exploration/htmx-demo.html
---

# Prozess-Übertrag — Design-Spec

## 1. Kontext und Motivation

Der bestehende Skill `guv-uebertrag` extrahiert den Kontennachweis der GuV aus
Jahresabschluss-PDFs und überträgt ihn in eine strukturierte Excel-Datei mit
Kontrollformeln nach HGB §275. Diese Aufgabe fällt im M&A-Alltag bei Calandi
regelmäßig an und ist heute ein manueller Claude-Code-Prozess.

Ziel dieses Projekts: Den Skill in eine **Web-App** überführen, die von mehreren
Team-Mitgliedern per Browser bedient werden kann. Eingabe per Drag-and-Drop,
automatische Verarbeitung im Hintergrund, Download einer fertigen Excel mit
vollständig verformelter Struktur.

## 2. Scope

### In Scope

- Upload von **Jahresabschluss-PDFs** (inkl. Kontennachweis der GuV)
- Upload von **BWA-PDFs** (betriebswirtschaftliche Auswertung)
- Automatische Typerkennung (JA vs. BWA)
- Extraktion per Claude API (text-PDFs und Scan-PDFs via Vision)
- Interaktive Review-UI für unsichere Zuordnungen
- Generierung einer **vollständig verformelten Excel** mit Mehrjahres-Konsolidierung
- Download der fertigen Excel
- Passwort-geschützter Zugang für Calandi-Team
- Automatisches Löschen aller Daten nach 24 Stunden

### Out of Scope

- Persistente Deal-Historie (ad-hoc-Nutzung)
- Mandantentrennung / Multi-Tenancy
- Registrierung, Rechnungsstellung, SaaS-Features
- Andere Dokumenttypen (Anlagenspiegel, SuSa-Listen, Gesellschafterverträge)
- Mobile-optimierte UI (Desktop-first reicht)
- Fancy Design — Funktionalität vor Ästhetik, "bestes Design" ist explizit kein Ziel

## 3. Nutzer und Zugriff

- **Nutzergruppe:** ca. 2-5 Calandi-Team-Mitglieder
- **Auth:** Ein gemeinsames Team-Passwort, bcrypt-gehasht als ENV-Variable,
  Session via signiertem Cookie. Keine Email, kein User-Tracking (anonym).
- **Zugriff:** Fester Link, nur via Passwort nutzbar.

## 4. Top-Anforderung: Vollständig verformelte Excel

Das ist der Kern des Tools — keine hardcoded Werte für Zwischensummen.

**Pflicht-Formeln in der generierten Excel:**

- Alle Zwischensummen pro Kategorie (`=SUM(...)`) — Umsatz, Material, Personal,
  Abschreibungen, sonst. betr. Aufwendungen inkl. Untergruppen (Raumkosten,
  Versicherungen, Fahrzeugkosten, etc.), EE-Steuern
- Ergebnis nach Steuern (Formel-Kaskade aus Zwischensummen)
- Jahresüberschuss (Ergebnis nach Steuern − Sonstige Steuern)
- Bilanzgewinn (JÜ ± Vortrag − Ausschüttung)
- EBITDA-Überleitung (Referenz-Formeln)
- Kennzahlen: Materialquote, Rohertragsquote, Personalquote, Umsatzrendite,
  EBITDA-Marge (alle als Formeln)
- Anteil-Spalten (% vom Umsatz) pro Jahr als Formel

**Cross-Check-Formeln am Ende der Excel:**

- `Berechneter JÜ (aus Formeln) vs. JÜ aus PDF` → bei Abweichung rot färben
- `Summe aller Hauptpositionen vs. Gesamtleistung` → Plausibilitäts-Check
- `Vorjahres-Kreuzvergleich` (Vorjahresspalte aus PDF n vs. PDF n-1)

**Ziel:** Doppelklick auf jede Zelle zeigt die Formel. Der User kann die
Korrektheit des Übertrags direkt in der Excel prüfen, ohne noch einmal auf
die PDFs schauen zu müssen.

## 5. Architektur

### Vereinfachter Single-Stack auf Railway

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser                                                        │
│  Login → Upload → Processing → Review → Download                │
│  (HTMX + Tailwind CDN, kein Build-Prozess)                      │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI auf Railway (Python, ein Service, ein Deploy)          │
│                                                                 │
│  • Jinja2-Templates rendern HTML                                │
│  • HTMX liefert partielle Updates (Upload, Polling, Review)     │
│  • BackgroundTasks für asynchrone Extraktion und Excel-Bau      │
│  • openpyxl für Excel-Generierung                               │
│  • Nutzt Logik aus skills/guv-uebertrag 1:1                     │
└────────┬──────────────────────────────────────┬─────────────────┘
         │                                      │
         ▼                                      ▼
┌─────────────────────┐              ┌──────────────────────────┐
│  Supabase (Free)    │              │  Claude API              │
│  • Storage (PDFs,   │              │  (claude-opus-4-7,       │
│    xlsx)            │              │   Vision für Scans)      │
│  • Postgres (Jobs)  │              │                          │
└─────────────────────┘              └──────────────────────────┘
```

### Komponenten

| Komponente | Rolle |
|---|---|
| FastAPI | Web-Framework, serviert HTML + API-Endpoints |
| Jinja2-Templates | Server-side-Rendering, kein JS-Build nötig |
| HTMX | Partielle Updates (Drag-Drop, Polling, Review-Submit) |
| Tailwind (CDN) | Styling ohne Build-Pipeline |
| BackgroundTasks | Asynchrone Verarbeitung in-Process (kein Celery/RQ) |
| openpyxl | Excel-Generierung mit Formeln |
| Supabase | Storage + Postgres (nur Job-State) |
| Claude API | PDF-Extraktion (Text + Vision für Scans) |

### Zentrale Entscheidungen und Begründungen

- **Single-Repo Python-Monolith:** Eine Sprache, ein Deploy, ein Log-Ort.
  Keine Koordination zwischen Frontend und Backend nötig.
- **HTMX statt React/Next.js:** Interne Tool-UX reicht, kein Build-Prozess,
  keine JS-Framework-Komplexität. Demo in `docs/exploration/htmx-demo.html`
  zeigt das Niveau.
- **Tailwind CDN statt Build:** Für interne Nutzung akzeptabel, spart
  komplette Build-Pipeline. Bei Bedarf später auf lokales Tailwind migrierbar.
- **BackgroundTasks statt Worker-Queue:** Bei 1-3 parallelen Jobs kein
  Bottleneck. Kein Celery, kein Redis nötig. Stale-Job-Recovery via
  pg_cron-Watchdog (Section 8).
- **Railway-Logs statt Logflare/Axiom:** Eingebautes Logging reicht.
- **Passwort-Auth statt Supabase Auth:** Weniger Komplexität.

## 6. Data Flow

### Phase 1: Upload → Extraktion

1. User zieht PDFs ins Upload-Fenster, klickt "Hochladen".
2. HTMX `POST /upload` mit Multipart-Form-Data.
3. FastAPI validiert (Dateityp PDF, max 10 MB/Datei, max 10 Dateien),
   speichert PDFs unter `jobs/{jobId}/input/*.pdf` in Supabase Storage.
4. Job-Record angelegt (`status=extracting`), BackgroundTask startet
   Extraktion, Response enthält HTMX-Redirect zu `/job/{jobId}`.
5. Browser pollt alle 3 Sekunden `/job/{jobId}/status` (HTMX).
6. Worker (asynchron, 30 s bis 20 min je nach Scan-Anteil):
   - Für jede PDF: Dateityp erkennen (Text vs. Scan via pymupdf)
   - Dateityp klassifizieren (JA vs. BWA) via Claude
   - Text-PDF: Text extrahieren, an Claude senden
   - Scan-PDF: Seiten als Bilder via Claude Vision
   - Claude liefert JSON: Konten, Gruppen, `confidence`-Flags
7. Worker speichert Extraktion in `jobs.extraction` (JSONB),
   setzt `status=review_needed`.
8. HTMX-Poll liefert beim nächsten Tick das Review-HTML zurück.

### Phase 2: Review → Excel

1. Review-Screen zeigt Liste unsicherer Zuordnungen (Konto + Betrag + Dropdown).
2. User bestätigt/korrigiert, klickt "Excel generieren".
3. HTMX `POST /job/{jobId}/finalize` mit Review-Antworten.
4. `status=finalizing`, BackgroundTask triggert Finalisierung.
5. Worker:
   - Merged Extraktion + Korrekturen
   - Mehrjahres-Konsolidierung (Schritt 5 aus `guv-uebertrag`)
   - Excel-Generierung mit openpyxl (siehe Section 4 — Top-Anforderung)
   - Plausibilitätsprüfungen (Summen-Check, JÜ-Check, Vorjahres-Cross-Check)
   - Speichert Datei unter `jobs/{jobId}/output.xlsx`
   - `status=ready`
6. UI zeigt Download-Button mit signed URL (15 min Gültigkeit).
7. Nach 24 h: pg_cron-Job löscht Job-Record und Storage-Dateien.

### Status-Modell

```
uploaded → extracting → review_needed → finalizing → ready
                ↓              ↓              ↓
              failed         failed         failed
                                              ↓
                                          expired (nach 24 h)
```

## 7. Datenmodell

### Supabase Postgres

```sql
create table jobs (
  id              uuid primary key default gen_random_uuid(),
  created_at      timestamptz default now(),
  status          text not null,
  input_files     jsonb not null,
  extraction      jsonb,
  review_answers  jsonb,
  output_path     text,
  error_message   text,
  expires_at      timestamptz not null
);

create index on jobs (status);
create index on jobs (expires_at);
```

### Supabase Storage

```
prozess-uebertrag/            (private bucket, keine public access)
└── {jobId}/
    ├── input/
    │   ├── ja-2022.pdf
    │   └── ...
    └── output.xlsx
```

### Cleanup

- `expires_at = created_at + 24 h`
- Supabase `pg_cron`: täglich `DELETE FROM jobs WHERE expires_at < now()`
- Storage-Cleanup via DB-Trigger (`BEFORE DELETE ON jobs`)

### Extraktions-JSON-Schema (Ausschnitt)

```json
{
  "documents": [
    {
      "file": "ja-2024.pdf",
      "type": "jahresabschluss",
      "source": "text",
      "year": 2024,
      "previous_year": 2023,
      "accounts": [
        {
          "konto_nr": "8400",
          "bezeichnung": "Erlöse 19% USt",
          "gruppe": "1. Umsatzerlöse",
          "betrag_gj": 1279228.53,
          "betrag_vj": 1110030.20,
          "confidence": "high"
        }
      ]
    }
  ],
  "open_questions": [
    {
      "document": "ja-2024.pdf",
      "konto_nr": "4980",
      "bezeichnung": "Sonstige betr. Aufwendungen",
      "betrag_gj": 12340.00,
      "suggested_groups": ["7g. Verschiedene betr. Kosten", "7d. Fahrzeugkosten"]
    }
  ]
}
```

## 8. Error Handling und Edge Cases

### Upload-Validierung

- Nur `.pdf`, max **10 MB pro Datei**, max 10 Dateien pro Job.
- Verschlüsselte PDFs werden erkannt und abgelehnt mit klarer Meldung.
- User müssen größere Dateien selbst verkleinern (z.B. via Preview, smallpdf).

### Scan-PDF-Handling

- Erkennung via pymupdf: falls auf der ersten Inhaltsseite < 100 Zeichen
  Text extrahierbar sind, wird die PDF als Scan klassifiziert.
- Scans gehen als Bilder an Claude Vision API.
- Zeit-Schätzung im UI: Text ~30-60 s, Scan ~2-4 min pro PDF.
- Warnhinweis im Review-Screen: "Zahlen aus Scan-PDFs kritisch prüfen."
- Kosten: Text ~0,10 €/PDF, Scan ~0,40-0,60 €/PDF.

### Claude-API-Fehler

- Rate-Limit (429): Retry mit exponential backoff, max 3 Versuche.
- Ungültiges JSON: 1x Retry, dann `status=failed`.
- API-Ausfall: `status=failed` mit Fehler-Message für User.

### Inhaltliche Checks in der generierten Excel

- Summen-Check: Excel-Formel-Summe vs. PDF-Summe pro Kategorie.
- Vorjahres-Cross-Check: Vorjahreswerte aus PDF(n) gegen PDF(n-1).
- Jahresüberschuss-Check: berechneter JÜ vs. JÜ in PDF.
- Abweichungen landen in Sheet "Fragen" der Excel und werden rot markiert.

### User-Verhalten

- Browser geschlossen während Job → Worker läuft weiter, Job-ID bleibt
  via Cookie/URL erreichbar.
- Abbruch im Review → Job bleibt `review_needed`, läuft mit `expires_at` ab.
- Rate-Limit: max 10 Uploads/Stunde pro Session.

### Worker-Ausfälle

- FastAPI-Prozess crasht → Jobs > 30 min in `extracting` oder `finalizing`
  werden von Watchdog auf `failed` gesetzt (30 min gibt Puffer für
  worst-case 5 Scan-PDFs sequenziell).
- Watchdog läuft als Supabase pg_cron Job alle 5 Minuten.

### Sicherheit

- Storage-URLs als signed URLs mit 15 min Ablauf.
- Job-IDs als UUIDs (nicht erratbar).
- Passwort als bcrypt-Hash in `APP_PASSWORD_HASH`, nicht im Code.
- Session-Cookie HttpOnly, Secure, SameSite=Strict.

## 9. Deployment

### Supabase

1. Neues Projekt `prozess-uebertrag` in bestehender Calandi-Org anlegen
   (nach Anlage ist Supabase Free-Tier erschöpft — 2 aktive Projekte max).
2. Migration `0001_jobs_table.sql` ausführen.
3. Storage-Bucket `prozess-uebertrag` (private) anlegen.
4. pg_cron Jobs einrichten: Cleanup (täglich), Watchdog (alle 5 min).

### Railway

1. Repo verbinden, Root auf `/` (Monorepo).
2. Dockerfile wird automatisch erkannt und gebaut.
3. ENV-Variablen:
   - `ANTHROPIC_API_KEY`
   - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
   - `APP_PASSWORD_HASH`
   - `SESSION_SECRET`
4. Auto-Deploy bei `git push`.
5. Railway liefert die öffentliche URL (z.B. `prozess-uebertrag.up.railway.app`).

### Lokale Entwicklung

- `docker-compose up` startet: Postgres (Supabase-Ersatz) + FastAPI-Server.
- Claude API wird immer live gerufen (kein Mock).
- Beispiel-PDFs unter `tests/fixtures/` zum Testen.

## 10. Kosten

| Posten | Kosten |
|---|---|
| Railway (FastAPI) | ~5 €/Monat |
| Supabase Free | 0 € |
| Claude API (pay-per-use) | ~0,10 €/Text-PDF, ~0,40-0,60 €/Scan-PDF |

**Erwartete monatliche Gesamtkosten** bei typischer Nutzung
(10 Deals × 3 PDFs = 30 PDFs/Monat): **~10-20 €/Monat**.

### Bekannte Free-Tier-Constraint

Supabase pausiert Projekte nach 1 Woche Inaktivität.
Bei unregelmäßiger Nutzung muss das Projekt im Dashboard kurz aktiviert werden.

## 11. Skill-Stack für die Umsetzung

| Phase | Skill | Zweck |
|---|---|---|
| Spec → Plan | `superpowers:writing-plans` | Implementierungs-Plan in Schritten |
| Claude API | `claude-api` | Prompt-Caching, Token-Optimierung |
| Supabase | `supabase-migration-assistant` | Schema-Migrationen versionieren |
| Implementieren | `superpowers:executing-plans` | Plan Schritt-für-Schritt |
| Tests | `superpowers:test-driven-development` | TDD für Kern-Extraktion und Excel-Formeln |
| Debugging | `superpowers:systematic-debugging` | Root-Cause-Analyse |
| Abnahme | `superpowers:verification-before-completion` | Vor jedem "fertig" prüfen |
| QA | `gstack-qa` | Browser-Tests des fertigen UI |
| Security | `supabase-security-audit` + `gstack-cso` | Pre-Launch-Check |
| Review | `gstack-review` + `superpowers:requesting-code-review` | Vor jedem Merge |
| Docs | `gstack-document-release` | README und CLAUDE.md für das Projekt |

## 12. Offene Punkte für die Implementierungsplanung

Wird in der `writing-plans`-Phase konkretisiert:

- Exakte Prompt-Struktur für Claude (Text-Mode vs. Vision-Mode, System-Prompts).
- Welche Test-Fixture-PDFs nutzen wir aus bestehenden Deals?
- Sollen Jinja-Templates und statische Assets im selben FastAPI-Projekt liegen
  oder als separates `/static` und `/templates`?
- Eingeloggt-Status via Cookie-Session oder simple HTTP Basic Auth?
  (Cookie ist user-freundlicher.)

## 13. Referenzen

- Bestehender Skill: `/Users/philippdegen/Documents/Claude/skills/guv-uebertrag/SKILL.md`
- HTMX-Demo: `./docs/exploration/htmx-demo.html`
- HGB §275 GKV-Gliederung (Basis der Excel-Struktur)
- Claude API: https://docs.anthropic.com/
- Supabase: https://supabase.com/docs
- Railway: https://docs.railway.app/
- HTMX: https://htmx.org/
- FastAPI: https://fastapi.tiangolo.com/
