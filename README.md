# Prozess-Übertrag

Interne Calandi-Web-App: Jahresabschluss-PDFs und BWAs per Drag-and-Drop
hochladen, Claude extrahiert den Kontennachweis der GuV, Output ist eine
vollständig verformelte Excel zum Download.

Siehe `docs/specs/2026-04-23-prozess-uebertrag-design.md` für das Design,
`docs/plans/2026-04-23-implementation-plan.md` für die Implementierungsschritte.

## Features

- Upload von Jahresabschluss-PDFs und BWAs (Text oder Scan)
- Automatische Typerkennung und Extraktion via Claude API
- Interaktive Review-UI für unsichere Zuordnungen
- Mehrjahres-Konsolidierung mit Cross-Check (Vorjahreswerte)
- Excel mit HGB §275 GKV-Gliederung, alle Summen als Formeln,
  EBITDA-Überleitung Top-Down + Bottom-Up + Plausibilitäts-Check
- Ad-hoc-Nutzung: alle Daten werden nach 24h automatisch gelöscht
- Team-Passwort, Session-Cookie, Rate-Limits

## Lokale Entwicklung

```bash
# venv anlegen und Dependencies installieren
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# .env aus .env.example erstellen, Credentials eintragen
cp .env.example .env
# -> SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY, APP_PASSWORD_HASH,
#    SESSION_SECRET, PUBLIC_BASE_URL

# Server starten
.venv/bin/uvicorn app.main:app --reload
# -> http://localhost:8000
```

### Tests

```bash
.venv/bin/pytest
```

Live-Smoketest gegen echte Claude-API (verbraucht API-Kosten):

```bash
.venv/bin/python3 tests/fixtures/smoketest_claude.py
```

## Docker (lokal)

```bash
docker compose up --build
# -> http://localhost:8000
```

## Deployment

### 1. Supabase-Projekt anlegen

1. Neues Projekt `prozess-uebertrag` in der Calandi-Org erstellen.
   Nach dieser Anlage ist das Free-Tier-Kontingent (max 2 aktive Projekte)
   ausgeschöpft.
2. SQL Editor → Inhalt von `supabase/migrations/0001_jobs_table.sql` einfügen
   und ausführen.
3. Database → Extensions → `pg_cron` aktivieren.
4. SQL Editor:
   ```sql
   select cron.schedule('cleanup-expired', '0 3 * * *', 'select cleanup_expired_jobs()');
   select cron.schedule('watchdog-stale', '*/5 * * * *', 'select watchdog_stale_jobs()');
   ```
5. Storage → New bucket → Name: `prozess-uebertrag` → **private**.

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

Passwort an Team weitergeben, HASH + SESSION_SECRET in Railway-Env eintragen.

### 3. Railway

1. Repo auf GitHub pushen.
2. Railway → New Project → Deploy from GitHub Repo → dieses Repo wählen.
3. Unter "Variables" setzen:
   - `ANTHROPIC_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY` (nicht der Anon-Key!)
   - `APP_PASSWORD_HASH`
   - `SESSION_SECRET`
   - `PUBLIC_BASE_URL` — später auf Railway-URL setzen
     (z.B. `https://prozess-uebertrag.up.railway.app`)
4. Auto-Deploy läuft bei jedem `git push`.
5. Railway zeigt die Public-URL. Einmal `PUBLIC_BASE_URL` darauf setzen und
   re-deployen, damit `Secure`-Cookies aktiv sind.

### 4. Health-Check

Nach Deploy:

```bash
curl https://deine-railway-url/health
# {"status":"ok"}
```

Wenn Supabase pausiert ist, kommt eine klare Fehlermeldung zurück (kein 500).

## Umgang mit Supabase-Pause (Free-Tier)

Bei >1 Woche Inaktivität pausiert Supabase das Projekt. Erste Anfrage danach
schlägt fehl. Manuell im Dashboard "Restore project" klicken, 1-2 Minuten
warten, dann läuft alles wieder.

## Bekannte Grenzen

- **Scan-PDFs dauern länger** (~2-4 min statt ~30-60s) und kosten mehr
  Claude-Tokens (~0,40-0,60 € statt ~0,10 € pro PDF).
- **Max 10 PDFs, je max 10 MB pro Upload.**
- **Rate-Limits**: 10 Uploads/Stunde pro Session, 10 Login-Versuche/15 min
  pro IP.
- **EBITDA-Check**: Top-Down und Bottom-Up sollten bei korrekter Extraktion
  identisch sein. Ein nonzero Check-Wert signalisiert Extraktionsprobleme.

## Wichtige Regeln für Weiterentwicklung

- Extraktions-Logik lebt in `app/worker/`, KEINE HTTP-Imports dort.
- Excel-Logik lebt in `app/excel/`, KEINE Netzwerk-Calls dort.
- **Alle Excel-Zwischensummen MÜSSEN Formeln sein** (`=SUM(...)`), nie hardcoded.
  Siehe `docs/specs/2026-04-23-prozess-uebertrag-design.md` Section 4.
- Änderungen an Prompts (`app/worker/prompts.py`) immer mit Live-Smoketest
  gegen echte Claude-API verifizieren.
