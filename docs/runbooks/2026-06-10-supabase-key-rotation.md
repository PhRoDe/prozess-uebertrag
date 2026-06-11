# Runbook: Supabase-Service-Key rotieren

**Wann:** Ursprünglich vor dem Hetzner-Cutover geplant (Stand 2026-06-10).

> [!warning]
> **Update nach Cutover (2026-06-11):** Die App läuft inzwischen live auf dem
> Calandi-Hetzner, **Railway ist abgeschaltet**. Der Live-Consumer des Keys ist
> jetzt die Server-`.env` (`/srv/calandi/uebertrag-stack/.env`), nicht mehr
> Railway. **Offen:** ob der dort eingetragene Key der rotierte oder noch der
> geleakte ist — mit Thomas/Leon verifizieren. Falls noch nicht rotiert: die
> Schritte unten gelten weiter, „Railway" überall durch den Hetzner-Container
> ersetzen (Thomas/Leon machen das server-seitig).

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
  | **Hetzner-Server-`.env`** (`/srv/calandi/uebertrag-stack/.env`) `SUPABASE_SERVICE_KEY` | **aktueller Live-Host** (seit Cutover 11.06.) — Container bricht bei Rotation ohne Update |
  | 1Password „Calandi/Prozess-Uebertrag" | Quelle der Wahrheit |
  | ~~Railway-Env~~ | abgeschaltet seit 11.06. — kein Consumer mehr |
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
widerrufen — ohne den laufenden Live-Betrieb (Hetzner-Container) zu unterbrechen.

1. Supabase-Dashboard (Calandi-Account) → Projekt `prozess-uebertrag` →
   **Settings → API Keys**.
2. Falls das neue Key-System noch nicht aktiv: aktivieren.
3. **Create new Secret key** (`sb_secret_…`). Wert wird **nur einmal**
   angezeigt → sofort in 1Password speichern.
4. Neuen Key bei allen Consumern eintragen (gleicher Var-Name
   `SUPABASE_SERVICE_KEY`):
   - [ ] 1Password-Item aktualisieren
   - [ ] Lokale `.env`
   - [ ] An Thomas/Leon für die Hetzner-Server-`.env` (via 1Password, nicht
     Mail/Chat) → sie tragen ihn ein + starten den Container neu
5. **Verifizieren** (siehe unten) — der Live-Stand (Hetzner) muss mit neuem Key grün sein.
6. Erst danach: **alten Legacy `service_role`-Key disablen/revoken** im
   Dashboard. Ab jetzt ist der geleakte Key tot.

## Option B — Legacy-JWT-Secret rotieren (nur falls neues System nicht da)

1. Dashboard → **Settings → API → JWT/Legacy Keys → Rotate**.
2. ⚠️ Rotiert `anon` **und** `service_role` gleichzeitig → der laufende
   Live-Betrieb (Hetzner-Container) bricht, bis die Env aktualisiert ist (kurze Downtime).
3. Reihenfolge eng koppeln: **Rotate → sofort** 1Password + lokale `.env` +
   Hetzner-Server-`.env` updaten → Container neu starten (Thomas/Leon).
4. `anon`-Key ändert sich mit — laut Consumer-Liste aber kein Consumer
   (server-gerendert). Vor Rotation kurz gegenchecken.

## Verifikation (nach Rotation, vor „alten Key tot")

- [ ] `curl https://uebertrag.calandi-tools.de/health` → `{"status":"ok"}`
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
