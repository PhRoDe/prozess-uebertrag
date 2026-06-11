# Cutover Railway → Calandi-Hetzner (Authentik) — abgeschlossen 2026-06-11

Historischer Verlauf der Migration von Railway auf den Calandi-Hetzner (Docker
hinter nginx + Authentik Forward-Auth). **Abgeschlossen am 2026-06-11.** Die App
läuft jetzt live auf `https://uebertrag.calandi-tools.de`, Auto-Deploy auf jeden
`main`-Push. Diese Datei ist die ausgelagerte „Migration"-Sektion der CLAUDE.md
(Cutover-Schritt 7) — als Referenz, nicht mehr als aktive Anleitung.

Quellen:
- `Downloads/2026-06-10-an-philipp-umbau-container-authentik.md` (Infra-Plan)
- `Downloads/2026-06-11-an-philipp-auth-umbau-spec.md` (Auth-Spec)
- `Downloads/2026-06-11-an-philipp-merge-schritt-fuer-schritt.md` (Merge-Freigabe)
- `Downloads/2026-06-11-briefing-fuer-philipps-claude.md` (Live-Betriebs-Regeln)

## Zielbild (umgesetzt)

App als Docker-Container auf dem Calandi-Hetzner hinter nginx. nginx macht
**Authentik Forward-Auth**; die App bekommt die Identität über Header
`X-Authentik-Username` / `-Email` / `-Groups`. Erreichbar unter
`https://uebertrag.calandi-tools.de` (Portal-Login, App-Auswahl). Deploy:
Push auf `main` → Auto-Deploy (`git reset --hard origin/main` →
`docker compose build` → `up -d` auf dem Server).

## Sicherheits-Stoppschilder (galten während des Cutovers)

1. **Login-Code erst entfernen, wenn die App NUR noch hinter Authentik hängt.**
   Solange offen auf Railway erreichbar, hätte das Entfernen des Passwort-Gates
   sie ungeschützt ins Netz gestellt. → eingehalten: Auth-Patch erst gemerged,
   als der Hetzner-Stand hinter Authentik bestätigt war.
2. ✅ Read-only Deploy-Key `calandi-server` eingetragen (2026-06-10). Repo war
   ohnehin schon privat.
3. **Supabase-Service-Key VOR der Secret-Übergabe rotieren** (geleakt 2026-04-24,
   Railway-Variables). Runbook: `docs/runbooks/2026-06-10-supabase-key-rotation.md`.
   ⚠️ Offen halten bis bestätigt: ob der in die Server-`.env` eingetragene Key der
   rotierte oder noch der geleakte ist — mit Thomas/Leon klären.

## Auth-Umbau (Login → Authentik)

- Eigenes Passwort-Gate / Session-Login entfernt; `APP_PASSWORD_HASH` und
  `SESSION_SECRET` vollständig aus `config.py` raus.
- `require_auth` prüft den `X-Authentik-Username`-Header; `current_user()` liest
  Username/Email/Groups aus den `X-Authentik-*`-Headern.
- Umgesetzt aus der Auth-Spec, **am 11.06. in `main` gemerged** (Merge `7c6f40e`),
  115 Tests grün. Freigabe zum Merge auf `main` kam von Thomas/Leon, weil
  Railway-Auto-Deploy zu dem Zeitpunkt aus war (main-Push triggerte nur CI).

## Secrets server-seitig (Container-`.env`, NIE ins Repo)

Liegen in `/srv/calandi/uebertrag-stack/.env`: `ANTHROPIC_API_KEY`,
`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `PUBLIC_BASE_URL`
(`=https://uebertrag.calandi-tools.de`). Übergabe lief über 1Password.

## Cutover-Reihenfolge (alle Schritte erledigt)

```
✅ 0. Deploy-Key eintragen (10.06.; Repo war schon privat)
✅ 1. Supabase-Service-Key rotieren → Secrets via 1Password an Thomas/Leon
✅ 2. Leon klont das Repo über den Deploy-Key
✅ 2b. "Bash(git push:*)" aus dem deny-Block entfernt (regulärer Push frei,
       destruktive Varianten bleiben geblockt)
✅ 3. Hetzner-Container hochgezogen, Webhook/Deploy getestet
✅ 4. uebertrag.calandi-tools.de hinter Authentik grün (/health + Test-Upload)
✅ 5. Auth-Patch gemerged (11.06., Merge 7c6f40e, Login raus)
✅ 6. Railway abgeschaltet
✅ 7. Diese Migrations-Sektion aus CLAUDE.md hierher ausgelagert
```

## Live-Betriebs-Regeln (aktiv, gelten dauerhaft)

Diese Regeln stehen forward-looking auch in der CLAUDE.md („Wichtige Regeln"):

- **Jeder Push auf `main` = sofortiger Live-Deploy.** Nie ohne grünes pytest.
- **Neue Pflicht-Env-Var ohne Default crasht den Live-Container** (pydantic
  `Settings`). → neue Config/Secrets vorher Thomas/Leon melden ODER Default geben.
- **Python-Deps in `pyproject.toml`** (`[project].dependencies`) — das Dockerfile
  installiert per `pip install -e .`. **Kein `requirements.txt`** (das Briefing
  nennt es fälschlich; im Repo gibt es keins).
- **DB-Migrationen manuell** im Supabase-Projekt ausführen, bevor der Code, der
  sie braucht, deployt.
- **Authentik-Login nicht zurückbauen** (kein Passwort-Gate/Session-Cookie/
  `app_password_hash`/`session_secret`).
