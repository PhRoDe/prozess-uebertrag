# Runbook: Supabase-Service-Key rotieren

**Wann:** Vor dem Hetzner-Cutover (Stand 2026-06-10). Blockiert die
Secret-Übergabe an die Calandi-Infra.

**Warum:** Der `service_role`-Key wurde am 2026-04-24 über die
Railway-Variablen exponiert und nie rotiert. Genau dieser Key soll jetzt an
Thomas/Leon für die Hetzner-`.env` raus. Ein kompromittierter Key darf nicht
in die neue Production-Umgebung wandern.

## Ausgangslage

- **Key-Typ:** Legacy `service_role`-JWT (`.env.example` zeigt `eyJ...`).
- **Wer nutzt den Key (Consumer-Liste):**
  | Stelle | Was |
  |---|---|
  | Lokale `.env` (Owner-Mac) | Entwicklung/Smoketests |
  | **Railway-Env** `SUPABASE_SERVICE_KEY` | **aktueller Live-Host** — bricht bei Rotation ohne Update |
  | 1Password „Calandi/Prozess-Uebertrag" | Quelle der Wahrheit |
  | Hetzner-`.env` (künftig) | bekommt den **neuen** Key via 1Password |
- **Code:** `app/db.py` + `app/storage.py` rufen
  `create_client(supabase_url, supabase_service_key)`. Nur server-seitig,
  `service_role`. Der Variablenname bleibt `SUPABASE_SERVICE_KEY`, egal welcher
  Key-Typ drinsteht → **kein Code-Change nötig**.
- **Kein anon-Key-Consumer:** Das Frontend ist server-gerendert (HTMX), nutzt
  kein `supabase-js`. Grep nach `service_role`/`SERVICE_KEY` findet nur die
  zwei `create_client`-Stellen.

> [!warning]
> **MCP kann das nicht.** Die hier verbundene Supabase-MCP sieht nur die Org
> mit „CFO Business/Private" — NICHT `prozess-uebertrag` (Calandi-Org,
> Frankfurt). Rotation also **manuell im Supabase-Dashboard** des
> Calandi-Accounts.

## Option A — Neuer API-Secret-Key (EMPFOHLEN, zero-downtime)

Supabase' neues Key-System (`sb_secret_…` / `sb_publishable_…`) erlaubt, einen
neuen Secret-Key zu erstellen und nur den **kompromittierten** Key zu
widerrufen — ohne den laufenden Railway-Betrieb zu unterbrechen.

1. Supabase-Dashboard (Calandi-Account) → Projekt `prozess-uebertrag` →
   **Settings → API Keys**.
2. Falls das neue Key-System noch nicht aktiv: aktivieren.
3. **Create new Secret key** (`sb_secret_…`). Wert wird **nur einmal**
   angezeigt → sofort in 1Password speichern.
4. Neuen Key bei allen Consumern eintragen (gleicher Var-Name
   `SUPABASE_SERVICE_KEY`):
   - [ ] 1Password-Item aktualisieren
   - [ ] Lokale `.env`
   - [ ] **Railway-Env** `SUPABASE_SERVICE_KEY` → Railway redeploy/restart
   - [ ] An Thomas/Leon für Hetzner-`.env` (via 1Password, nicht Mail/Chat)
5. **Verifizieren** (siehe unten) — Railway muss mit neuem Key grün sein.
6. Erst danach: **alten Legacy `service_role`-Key disablen/revoken** im
   Dashboard. Ab jetzt ist der geleakte Key tot.

## Option B — Legacy-JWT-Secret rotieren (nur falls neues System nicht da)

1. Dashboard → **Settings → API → JWT/Legacy Keys → Rotate**.
2. ⚠️ Rotiert `anon` **und** `service_role` gleichzeitig → der laufende
   Railway-Betrieb bricht, bis die Env aktualisiert ist (kurze Downtime).
3. Reihenfolge eng koppeln: **Rotate → sofort** 1Password + lokale `.env` +
   Railway-Env updaten → Railway restart.
4. `anon`-Key ändert sich mit — laut Consumer-Liste aber kein Consumer
   (server-gerendert). Vor Rotation kurz gegenchecken.

## Verifikation (nach Rotation, vor „alten Key tot")

- [ ] `curl https://<aktueller-railway-host>/health` → `{"status":"ok"}`
- [ ] Ein echter Test-Upload erzeugt eine Excel (testet Storage **und** DB mit
      neuem Key)
- [ ] (optional) Alten Key gegen die REST-API testen → muss `401` liefern

## Einordnung in den Cutover

Das ist **Schritt 1** der Cutover-Reihenfolge (siehe `CLAUDE.md`, Abschnitt
„Migration auf Calandi-Tools"). Thomas/Leon bekommen **nur den neuen** Key.

## Zusatz-Empfehlung (über den dokumentierten TODO hinaus)

Der Railway-Variablen-Leak hat **alle** damaligen Env-Vars potenziell
exponiert — nicht nur den Supabase-Key. Erwägen, bei dieser Gelegenheit auch
zu rotieren:

- [ ] `ANTHROPIC_API_KEY` (Anthropic-Console → neuer Key, alten widerrufen)
- [ ] `SESSION_SECRET` (entfällt evtl. eh mit Authentik — mit Thomas klären)
- [ ] Supabase `anon`-Key (falls irgendwo genutzt — aktuell kein Consumer)
