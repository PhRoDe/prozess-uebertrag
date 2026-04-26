# Prozess-Übertrag

Interne Calandi-Web-App: Jahresabschluss- und BWA-PDFs per Drag-and-Drop
hochladen, Claude extrahiert den Kontennachweis der GuV, Output ist eine Excel
die die Gliederung der Original-PDF 1:1 übernimmt.

**Live:** https://prozess-uebertrag-production.up.railway.app

Siehe `CLAUDE.md` für die Entwickler-Perspektive,
`docs/specs/2026-04-23-prozess-uebertrag-design.md` für das Design.

## Features

- Upload von Jahresabschluss-PDFs (Text oder Scan) und BWAs
- Automatische Typerkennung (JA vs. BWA) via Claude
- Extraktion pro PDF, Multi-Jahres-Konsolidierung mit Cross-Check
- **Excel übernimmt die PDF-Gliederung** + GKV-Sektion-Klassifikation (§275 HGB)
  als STB-unabhängiger Anker für Multi-Jahr-Matching
- **Plausibilitäts-Anker:** Excel zeigt PDF-JÜ + Differenz-Zeile am Ende; Diff
  > 1 ct erscheint automatisch im Fragen-Sheet
- **Bilanzgewinn-Block** (Gewinnvortrag, Ausschüttung, Bilanzgewinn) als eigene
  Sektion nach dem Jahresergebnis mit Bilanzgewinn-Formel
- Vorjahres-Mismatch- und Summen-Mismatch-Checks im separaten "Fragen"-Sheet
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

Nur das "Übertrag"-Sheet plus ein "Fragen"-Sheet mit Datenqualitäts-Hinweisen.

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

```bash
.venv/bin/python3 -c "
import secrets, string, bcrypt
pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
print('PASSWORD=', pw)
print('HASH=', bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode())
print('SESSION_SECRET=', secrets.token_hex(32))
"
```

Passwort an Team weitergeben, Hash + Secret als Railway-Env setzen.

### 3. Railway

```bash
# einmaliges Setup
railway login
railway init   # Projekt anlegen
railway variables --set KEY=VALUE ...  # alle ENV-Vars aus .env
railway up     # Deploy
railway domain # Public-URL generieren
railway variables --set PUBLIC_BASE_URL=https://…  # Public-URL eintragen
railway up     # Re-Deploy mit Secure-Cookie
```

### 4. Health-Check

```bash
curl https://deine-railway-url/health
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

```bash
# Änderungen machen, Tests grün bekommen
.venv/bin/pytest

# Backup + Historie auf GitHub
git add . && git commit -m "…" && git push

# Deploy auf Railway
railway up
```

## Wichtige Regeln (für weitere Entwicklung)

- Extraktions-Logik lebt in `app/worker/`, KEINE HTTP-Imports dort.
- Excel-Logik lebt in `app/excel/`, KEINE Netzwerk-Calls dort.
- **Alle Excel-Zwischensummen MÜSSEN Formeln sein** (`=SUM(...)`), nie hardcoded.
- **Keine HGB-Normalisierung der Reihenfolge**: Wenn eine PDF "Raumkosten" als
  Hauptgruppe zeigt, bleibt das so. Aber `gkv_section`-Slug pro Gruppe ist
  Pflicht (semantischer Anker für JÜ-Formel + Cross-Year-Matching).
- Claude übernimmt Vorzeichen **1:1 aus der PDF**. Die `sign_convention` wird
  im Builder aus den Daten abgeleitet, nicht blind aus Claude übernommen.
- `pdf_jahresueberschuss_gj/_vj` ist Pflicht — Builder zeigt PDF-JÜ vs
  Excel-JÜ-Diff am Ende, Diff > 1 ct landet im Fragen-Sheet.
- Änderungen an Prompts (`app/worker/prompts.py`) immer mit
  `tests/fixtures/smoketest_e2e.py` gegen echte JA-PDFs verifizieren.
