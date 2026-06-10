# Prozess-Übertrag

Interne Calandi-Web-App: Jahresabschluss-, BWA- und Susa-PDFs per
Drag-and-Drop hochladen, Claude extrahiert die Konten, Output ist eine
Excel die die Gliederung der Original-PDF 1:1 übernimmt.

**Live:** aktuell noch auf Railway; **Migration auf
https://uebertrag.calandi-tools.de** (Hetzner + Authentik) läuft —
siehe `CLAUDE.md`, Abschnitt "Migration auf Calandi-Tools".

Siehe `CLAUDE.md` für die Entwickler-Perspektive,
`docs/specs/2026-04-23-prozess-uebertrag-design.md` für das Design.

## Unterstützte Eingangsformate

- **HGB-GuV nach §275** (Kapitalgesellschaften) mit Kontennachweis,
  Endwert = Jahresüberschuss/Jahresfehlbetrag
- **EÜR nach §4 Abs 3 EStG** (Einzelunternehmer/Freiberufler) mit
  Hinzurechnungen + Kürzungen, Endwert = Steuerlicher Gewinn
- **BWA** (Betriebswirtschaftliche Auswertung) — kurz/Hauptpositionen oder
  detailliert mit Einzelkonten
- **Susa** (Summen- und Saldenliste / DATEV-Roh-Auszug) — Konten der
  Klassen 2-8; Klasse 0/1/9 (Bilanz, Saldenvorträge) werden ausgeschlossen

Mehrere Dokumente lassen sich kombinieren (z.B. JA 2023 + JA 2024 + BWA 2025
+ Susa Dez 2025) — wird automatisch zu einer Excel mit einer Spalte pro
Periode konsolidiert (Cross-Year-Matching via Kontonummer).

## Features

- Upload von PDFs (Text oder Scan)
- Automatische Typerkennung (jahresabschluss / bwa / susa) via Claude
- Extraktion pro PDF, Multi-Jahres-Konsolidierung mit Cross-Check
- **Excel übernimmt die PDF-Gliederung** + GKV-Sektion-Klassifikation (§275 HGB)
  als STB-unabhängiger Anker für Multi-Jahr-Matching
- **Plausibilitäts-Anker mit hartem Fail:** Excel-Endwert wird centgenau
  gegen den PDF-Endwert verglichen (Jahresüberschuss bei HGB-GuV /
  Steuerlicher Gewinn bei EÜR). Diff > 1 ct → Job FAILT, kein silently
  fehlerhaftes Excel wird ausgeliefert. Bei Susa kein Cross-Check
  (kein Endwert in der Susa selbst)
- **Bestandsveränderung universal**: Erhöhung/Verminderung-Position wird per
  Name normalisiert (`+|wert|` für Erhöhung, `-|wert|` für Verminderung) —
  egal welche STB-Vorzeichen-Konvention das PDF nutzt
- **Bilanzgewinn-Block** (Gewinnvortrag, Ausschüttung, Bilanzgewinn) als eigene
  Sektion nach dem Jahresergebnis mit Bilanzgewinn-Formel
- **Stille Auflösung von Audit-Mismatches**: Eigenjahres-Werte gewinnen gegen
  VJ-Werte aus Folge-JAs (klassische STB-Vorzeichen-Inversion); Konten-Summe
  ist authoritativ über pdf_sum_gj (fängt Claude-Übertrags-Doppelzählung)
- **BWA-Aggregat-Doppelzählung verhindert**: Wenn eine Spalte schon Konten-Daten
  in JA-Top-Level-Gruppen hat, werden reine BWA-Aggregat-Sichten in der
  JÜ-Formel übersprungen
- "Fragen"-Sheet wird **nur** angelegt wenn echte User-Entscheidungen offen
  sind (z.B. abgeschnittene Konto-Bezeichnungen) — bei sauberen Läufen: kein Sheet
- Ad-hoc-Nutzung, alle Dateien werden nach 24h automatisch gelöscht
- Team-Passwort-Login, Session-Cookie, Brute-Force- und Upload-Rate-Limit

## Wie es aussieht

Input: mehrere Jahresabschluss-PDFs verschiedener Jahre.

Output: ein Excel-Sheet "Übertrag" mit:

| Konto | Bezeichnung | 2023 | 2024 | 2025 | BWA 2025 |
|---|---|---|---|---|---|
| | **1. Umsatzerlöse** | `=SUM(…)` | `=SUM(…)` | `=SUM(…)` | 500.000,00 |
| 8300 | Erlöse 7 % USt | 51.434,95 | 44.138,08 | 42.606,25 | |
| 8310 | Erlöse EG 7 % | | 3.768,15 | 311,98 | |
| | **3. Materialaufwand** | `=SUM(…)` | `=SUM(…)` | `=SUM(…)` | -180.000,00 |
| 3106 | Fremdleistungen | -4.147,32 | -3.623,36 | -8.631,33 | |
| … | | | | | |
| | **Jahresergebnis** | `=C… +C… -C… …` | | | |

Nur das "Übertrag"-Sheet — und (nur bei tatsächlich offenen User-Entscheidungen) ein "Fragen"-Sheet.

## Lokale Entwicklung

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env
# ausfüllen: SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY,
# APP_PASSWORD_HASH, SESSION_SECRET, PUBLIC_BASE_URL

.venv/bin/uvicorn app.main:app --reload
# → http://localhost:8000
```

## Tests

```bash
.venv/bin/pytest                              # Unit-Tests, ~3s
.venv/bin/python tests/fixtures/smoketest_claude.py  # Live Claude API mit synthetischer Mini-PDF (~0,10 €)
.venv/bin/python tests/fixtures/smoketest_e2e.py "<pfad/ja.pdf>" ...  # E2E gegen echte JA-PDFs, schreibt smoketest_output.xlsx
```

## Docker lokal

```bash
docker compose up --build
# → http://localhost:8000
```

## Deployment

### 1. Supabase

1. Projekt `prozess-uebertrag` in der Calandi-Org anlegen (Free-Tier, Frankfurt).
2. SQL Editor → Inhalt von `supabase/migrations/0001_jobs_table.sql` ausführen.
3. Database → Extensions → `pg_cron` aktivieren.
4. SQL Editor:
   ```sql
   select cron.schedule('cleanup-expired', '0 3 * * *', 'select cleanup_expired_jobs()');
   select cron.schedule('watchdog-stale', '*/5 * * * *', 'select watchdog_stale_jobs()');
   ```
5. Storage → New bucket → Name `prozess-uebertrag`, **private**.

### 2. Team-Passwort generieren

> **Migration:** Entfällt nach dem Hetzner-Cutover — Auth übernimmt dann
> **Authentik** (nginx Forward-Auth), das eigene Passwort-Gate
> (`APP_PASSWORD_HASH`) wird per Patch entfernt. Bis dahin gilt das Folgende.

```bash
.venv/bin/python3 -c "
import secrets, string, bcrypt
pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
print('PASSWORD=', pw)
print('HASH=', bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode())
print('SESSION_SECRET=', secrets.token_hex(32))
"
```

Passwort an Team weitergeben, Hash + Secret als Server-Env (bzw.
GitHub-Secret) setzen.

### 3. Server (Calandi-Hetzner, Docker) — in Migration

Ziel: App läuft als Docker-Container auf dem Calandi-Hetzner hinter nginx
(Authentik Forward-Auth) unter `uebertrag.calandi-tools.de`. Deploy:

```
git push main (GitHub)  →  Webhook …/hooks/deploy-uebertrag  →  Container-Rebuild
```

Server-Setup (Container, nginx/Authentik, Env-Vars, Webhook) macht die
Calandi-Infra (Thomas/Leon), nicht dieses Repo. Der read-only Deploy-Key
`calandi-server` ist bereits eingetragen (2026-06-10), das Repo ist privat —
Leon klont darüber. Alle ENV-Vars aus `.env.example` müssen server-seitig
gesetzt sein (inkl. `PUBLIC_BASE_URL=https://uebertrag.calandi-tools.de`).
Übergabe der Secrets über 1Password. Cutover-Reihenfolge + Sicherheits-Stopps:
`CLAUDE.md`, Abschnitt "Migration auf Calandi-Tools".

### 4. Health-Check

```bash
curl https://uebertrag.calandi-tools.de/health   # nach Cutover
# {"status":"ok"}
```

Bei pausierter Supabase kommt eine klare Fehlermeldung, kein 500.

## Umgang mit Supabase-Pause (Free-Tier)

Supabase pausiert Projekte nach 1 Woche Inaktivität. Im Dashboard "Restore
project" klicken, 1-2 Minuten warten, dann läuft alles wieder.

## Bekannte Grenzen

- **Scan-PDFs** dauern 2-4 min (statt 30-60s) und kosten ~0,40-0,60 €/PDF
  (Claude Vision) statt ~0,10 €.
- **Max 10 PDFs, je max 10 MB pro Upload.**
- **Rate-Limits:** 10 Uploads/Stunde pro Session+IP, 10 Login-Versuche/15 min
  pro IP.
- **Offener Punkt:** Supabase-Service-Key wurde während Setup einmal exponiert
  — vor echtem Deal-Upload im Dashboard rotieren
  (Settings → API → JWT Keys).

## Projekt-Update-Workflow

Seit 2026-06-10: Deploy = Push auf GitHub. Der Webhook zieht den Stand
automatisch auf den Calandi-Server.

```bash
# Änderungen machen, Tests grün bekommen
.venv/bin/pytest

# Push = Deploy (Webhook zieht auf den Server)
git add . && git commit -m "…" && git push

# bequemer: Pre-Push-Gate (läuft pytest, pusht nur bei grün)
./bin/deploy.sh
```

Solo-Entwicklung: `./bin/deploy.sh` (pytest grün → push) reicht als Schutz,
Direkt-Push auf `main` ist OK. Eine PR-/Branch-Protection-Pflicht braucht es
erst, wenn ein zweiter Entwickler dazukommt.

## Wichtige Regeln (für weitere Entwicklung)

- Extraktions-Logik lebt in `app/worker/`, KEINE HTTP-Imports dort.
- Excel-Logik lebt in `app/excel/`, KEINE Netzwerk-Calls dort.
- **Alle Excel-Zwischensummen MÜSSEN Formeln sein** (`=SUM(...)`), nie hardcoded.
- **Keine HGB-Normalisierung der Reihenfolge**: Wenn eine PDF "Raumkosten" als
  Hauptgruppe zeigt, bleibt das so. `gkv_section`-Slug ist optional —
  bei HGB-Positionen vergeben (semantischer Anker für JÜ-Formel +
  Cross-Year-Matching), bei EÜR-spezifischen Positionen (Privatanteile,
  IAB, Hinzurechnungen, Kürzungen) `null` setzen.
- **EÜR-Korrekturen**: Hinzurechnungen → `type="ertrag"` (werden zum
  Steuerlichen Gewinn addiert), Kürzungen → `type="aufwand"` (werden
  subtrahiert). Mit `expenses_positive` ergibt das die korrekte
  Steuer-Gewinn-Formel.
- **Synthetische Parent-Gruppen mit gemischten Sub-Types** (typisch EÜR
  "D. STEUERLICHE KORREKTUREN" mit Hinzurechnungen=ertrag +
  Kürzungen=aufwand) werden in der Endwert-Formel **auf Sub-Group-Ebene**
  iteriert, NICHT als Parent-Block. Der Parent-Wrapper kann mixed-type
  Subs nicht repräsentieren.
- Claude übernimmt Vorzeichen **1:1 aus der PDF**. Die `sign_convention` wird
  im Builder aus den Daten abgeleitet, nicht blind aus Claude übernommen.
- `pdf_jahresueberschuss_gj/_vj` ist Pflicht — Builder vergleicht Excel-JÜ
  numerisch gegen PDF-JÜ und wirft `ValueError` bei Diff > 1 ct (Job FAILT).
- `previous_year_mismatch` und `group_sum_mismatch` werden **stillschweigend
  aufgelöst** (Eigenjahr / Konten-Summe authoritativ), nicht im Fragen-Sheet.
- `pdf_sum_gj` von Claude **niemals selbst rechnen lassen** — Claude darf den
  Wert nur wörtlich aus dem PDF liefern (Übertrags-Zeilen am Seitenende sind
  KEINE Gruppen-Summen). Konten-Summe gewinnt sowieso bei Konflikt.
- Änderungen an Prompts (`app/worker/prompts.py`) immer mit
  `tests/fixtures/smoketest_e2e.py` gegen echte JA-PDFs verifizieren.
