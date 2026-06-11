# Prozess-Übertrag

Interne Calandi-Web-App. JA-, BWA- und Susa-PDFs per Drag-and-Drop hochladen,
Claude extrahiert die Konten, Output ist eine Excel die die **Gliederung der
Original-PDF 1:1 übernimmt** (nicht HGB-normalisiert).

**Unterstützte Formate (seit 2026-04-27):**
- **HGB-GuV §275** (Kapitalgesellschaften)
- **EÜR §4 Abs 3 EStG** (Einzelunternehmer/Freiberufler) inkl.
  Hinzurechnungen + Kürzungen → Steuerlicher Gewinn
- **BWA** (kurz oder detailliert mit Konten)
- **Susa** (DATEV-Roh-Saldenliste, Klassen 2-8; Bilanz/Saldenvorträge raus)

## Wo es läuft

> [!warning]
> **Migration läuft (Stand 2026-06-10).** Die App zieht von **Railway** auf
> den **Calandi-Hetzner** (Docker-Container hinter nginx + Authentik). Bis der
> Hetzner-Stand verifiziert grün ist, ist **Railway weiter der Live-Host**.
> Cutover-Reihenfolge + offene Punkte siehe Abschnitt **"Migration auf
> Calandi-Tools (Hetzner + Authentik)"** unten.

| Was | Aktuell (Railway) | Ziel (Hetzner/Calandi-Tools) |
|---|---|---|
| Live-App | Railway-URL (noch aktiv) | https://uebertrag.calandi-tools.de |
| Host | Railway | Hetzner, Docker-Container hinter nginx (intern Port 8000) |
| Deploy | `railway up` (Alt) | `git push` auf `main` → Webhook `…/hooks/deploy-uebertrag` → Container-Rebuild |
| Auth | eigenes Passwort-Gate | **Authentik Forward-Auth** (nginx), Identität via Header |
| Code-Repo | https://github.com/PhRoDe/prozess-uebertrag (privat) | privat, read-only Deploy-Key `calandi-server` eingetragen (2026-06-10) |
| Supabase | Projekt `prozess-uebertrag` (Frankfurt) | unverändert |
| Team-Passwort | `TEAM_CREDENTIALS.local.md` (gitignored) | entfällt mit Authentik |

**Ziel-Deploy-Workflow (nach Cutover):** Entwickeln lokal → `git push` auf
`main` → GitHub-Actions-CI läuft → Webhook triggert den Container-Rebuild auf
Hetzner. GitHub ist die einzige Quelle der Wahrheit, der Server folgt
automatisch. Bis dahin gilt für Live noch Railway.

## Erstes Setup (neue Sessions / Team-Mitglieder)

```bash
git clone https://github.com/PhRoDe/prozess-uebertrag.git
cd prozess-uebertrag
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# → ANTHROPIC_API_KEY + SUPABASE_URL + SUPABASE_SERVICE_KEY eintragen
#   (Werte in 1Password "Calandi/Prozess-Uebertrag"). Auth läuft über
#   Authentik (Forward-Auth) — kein App-Passwort, kein SESSION_SECRET mehr.
.venv/bin/pytest                                  # 115 Tests müssen grün sein
.venv/bin/uvicorn app.main:app --reload           # http://localhost:8000
# Lokal: geschützte Routen brauchen den X-Authentik-Username-Header (injiziert
# nur nginx). Lokal faken, z.B. curl -H "X-Authentik-Username: dev" …
```

Voraussetzungen: Python 3.12+. Nach dem Cutover ist kein Railway-CLI mehr
nötig — Deploy läuft dann über GitHub-Push + Webhook (siehe oben).

### Claude-Code-Setup (`.claude/settings.json`)

`defaultMode: bypassPermissions` (wie Nylo) — Tools laufen ohne Rückfrage. Mit
zwei Schutzschichten:
- **`ask`** (prompten trotz Bypass): `python3`, `curl` (Exec-/Netzwerk-Primitive,
  Security-Review). `.venv/bin/python3` + `.venv/bin/pytest` fangen mit `.venv/`
  an → **nicht** betroffen, laufen ohne Prompt.
- **`deny`** (immer blockiert): `rm -rf`, `git reset --hard` sowie destruktive
  `git push`-Varianten (`--force`, `-f`, `--force-with-lease`, `--mirror`,
  `--delete`). Regulärer `git push` ist seit Cutover-Schritt 2b (erledigt
  2026-06-11) frei — nur die history-/branch-zerstörenden Varianten bleiben
  geblockt. Deny ist prefix-basiert, also Leitplanke gegen Versehen, kein
  dichter Riegel.

## Architektur in einem Satz

FastAPI-Monolith im Docker-Container (in Migration: Railway → Calandi-Hetzner
hinter nginx/Authentik), HTMX-UI mit Tailwind-CDN, Claude-API für
PDF-Extraktion, Supabase für Storage+Postgres, alles in einem Python-Container.

### Datenfluss

```
                      Browser (HTMX)
                            │ Drag-and-Drop PDFs
                            ▼
   ┌─────────────────────────────────────────────────┐
   │  app/routes/upload.py  · Auth · Rate-Limit       │
   │  app/routes/job.py     · Status-Polling          │
   └─────────────────────────────────────────────────┘
                            │ jobs.row + storage.upload
                            ▼
                  Supabase (Postgres + Storage)
                            │
                            │ Worker claimt Job (Idempotent)
                            ▼
   ┌─────────────────────────────────────────────────┐
   │  app/worker/tasks.py   · Background-Orchestrator │
   │      │                                            │
   │      ├─► claude_client.py  · doc_type-aware      │
   │      │       (jahresabschluss / bwa / susa)       │
   │      │       prompts.py    · JSON-Schema           │
   │      │                                            │
   │      ├─► consolidate.py    · Multi-Jahr-Merge    │
   │      │       · Vorzeichen-Normalisierung          │
   │      │       · Cross-Year-Routing                  │
   │      │                                            │
   │      └─► excel/builder.py  · Layout + Formeln    │
   │              · Build-Time-Cross-Check (Excel-JÜ   │
   │                ↔ PDF-JÜ centgenau, sonst FAIL)    │
   └─────────────────────────────────────────────────┘
                            │ xlsx-Bytes
                            ▼
                  Supabase Storage
                            │
                            ▼
                      Browser-Download
```

Pipeline-Hinweise: Worker ist idempotent + Claim-basiert (kein Doppel-Run). Cross-Check
am Ende von `build_excel` ist Fail-Loud — kein silent kaputtes Excel wird ausgeliefert.

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
| `app/worker/prompts.py` | Extraktions-Prompts für Claude (HGB-GuV+EÜR / BWA / Susa) — Quelle der Wahrheit für "wie soll das JSON aussehen" |
| `app/worker/claude_client.py` | anthropic SDK Wrapper, doc_type-aware (`extract_text_pdf(doc_type=...)`), 429-Retry, `<pdf_content>`-Delimiter |
| `app/worker/consolidate.py` | Multi-Jahr-Merging, Spalten-Bau (JA+BWA getrennt), Vorjahres-Cross-Check |
| `app/excel/builder.py` | Dynamisches Layout, Summe-zuerst, sign-aware Jahresergebnis |
| `app/routes/pages.py` | Login, Home, Logout |
| `app/routes/upload.py` | PDF-Upload, Rate-Limit, Filename-Sanitize |
| `app/routes/job.py` | Status-Polling, Review-Screen, Finalize |
| `app/worker/tasks.py` | Background-Orchestrator, Idempotent + Claim-Pattern |
| `app/ratelimit.py` | In-Memory-Limiter, X-Forwarded-For-aware (hinter Reverse-Proxy) |

## Wichtige Regeln (nicht brechen)

Tiefes „Warum" zu den Vorzeichen-/Routing-Regeln steht in
`~/.claude/rules/pipeline-engineering.md` (6a–6e, global geladen) — hier nur
der code-spezifische Kern.

**Architektur + Output-Format:**
- `app/worker/` = Extraktion, **KEINE HTTP-Imports**. `app/excel/` = Output,
  **KEINE Netzwerk-Calls**.
- **Alle Excel-Zwischensummen MÜSSEN Formeln sein** (`=SUM(...)` / Kaskaden),
  nie hardcoded.
- **Excel-Zahlenformat** `'#,##0.00;-#,##0.00'` — kein `[Red]`, neutrales Minus.
- **Keine HGB-Normalisierung der Reihenfolge** — PDF-Gliederung 1:1;
  `gkv_section` liefert die semantische Standardisierung.

**Vorzeichen + JÜ-Formel:**
- Werte behalten **ihr PDF-Vorzeichen** (Claude normalisiert nicht). JÜ-Formel
  unterscheidet `expenses_negative`/`expenses_positive`; `sign_convention` wird
  im Builder aus den Daten abgeleitet, nicht aus Claude.
- **`gkv_section` authoritativ über `type`** für die JÜ-Klassifikation
  (`SECTION_ROLE` in `builder.py`) — fängt `type`-Drift (z.B. Steuern "neutral").
- **Bestandsveränderung universal** (`_normalize_bestand_value`): bei
  `gkv_section="bestandsveraenderung"` ist der Name authoritativ ("Erhöhung"
  → `+|wert|`, "Verminderung" → `-|wert|`), eingehendes Vorzeichen ignoriert.
- **Sign-Outlier pro Spalte** (`_normalize_column_signs`): invertiert Claude
  **eine ganze Spalte** (Trigger: Suffix-Minus an "Übertrag"-Zwischensummen),
  wird's per Mehrheits-Vote (>50 %, ≥2 Spalten) erkannt + ×−1 korrigiert.

**Doppelzählung + Routing:**
- **BWA-Aggregat-Doppelzählung**: Aggregat-Gruppen ohne eigene Konten in der
  JÜ-Formel überspringen wenn die Spalte schon JA-Konten hat
  (`col_has_account_data and not g.get("accounts")`).
- **Synthetic-Parent verliert beim Section-Routing** gegen reale Subs
  (`_build_section_to_tpl`, Marker `_synthetic_parent`; Rang real-Top > Sub >
  synthetic-Top) — sonst Doppelzählung bei Hierarchie-Mix zwischen Jahren.
- **EÜR Hinzurechnungen/Kürzungen**: Hinzurechnungen `type="ertrag"`,
  Kürzungen `type="aufwand"`. `_endwert_groups()` iteriert synthetic Parents
  (mixed-type Subs, z.B. "D. STEUERLICHE KORREKTUREN") auf Sub-Group-Ebene.

**Persistenz + Cross-Check:**
- **JSON-Roundtrip-sicher**: int-Keys in `values: {col_idx: ...}` werden in
  Postgres JSONB zu Strings; `_coerce_int_keys` castet beim Eintreten zurück.
  Nie direkt mit int zugreifen.
- **Build-Time-Plausibilitäts-Anker**: Excel-Endwert ↔ PDF-Endwert centgenau;
  Diff > 1 ct → `ValueError` aus `build_excel` (Job FAILED). `endwert_label`
  dynamisch (HGB: Jahresüberschuss / EÜR: Steuerlicher Gewinn §4 Abs 3). Susa:
  kein Cross-Check.
- **Susa-Filterung**: Klassen 0/1/9 (Bilanz, Saldenvorträge) in `SUSA_PROMPT`
  ausgeschlossen.

**Audit + Secrets:**
- **Stille Auflösung von Mismatches**: `previous_year_mismatch` → Eigenjahr
  authoritativ (`setdefault`); `group_sum_mismatch` → Konten-Summe authoritativ
  über `pdf_sum_gj`; `unmatched_account` bleibt im Fragen-Sheet (echte
  User-Entscheidung).
- **Sensitive Daten nie in stdout/Commits** — Env-Vars nur als Name nennen,
  nie Klartext-Werte.

## Typische Workflows

### Lokal testen
```bash
cd "/Users/philippdegen/Documents/Claude/Calandi/Prozess-Übertrag"
.venv/bin/uvicorn app.main:app --reload
# → http://localhost:8000. Auth via Authentik-Header: geschützte Routen lokal
#   mit  curl -H "X-Authentik-Username: dev" …  ansprechen (nginx fehlt lokal).
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

> [!warning]
> **Während der Migration:** Der Hetzner-Webhook ist noch nicht final
> verifiziert. Bis Hetzner grün ist, läuft Live noch auf Railway. Erst nach
> bestätigtem Cutover (siehe "Migration auf Calandi-Tools") ist `git push` =
> Live-Deploy.

Ziel-Deploy = **Push auf `main`**. Der Webhook (`…/hooks/deploy-uebertrag`)
triggert den Container-Rebuild auf Hetzner. Kein `railway up`, kein manueller
Server-Zugriff.

```bash
# Lokales Pre-Push-Gate: blockt den Push wenn pytest rot
./bin/deploy.sh
```
Das Skript läuft pytest → bei rot ABORT. Bei grün: prüft uncommitted changes,
fragt nach Bestätigung wenn welche da sind, **pusht dann auf GitHub** (`git
push`). Der Push triggert die GitHub-Actions-CI **und** den Deploy-Webhook.

**Niemals pushen ohne grünes pytest** — das umgeht den Production-Schutz und
schiebt kaputten Code direkt auf die Live-App.

**Single-Developer-Setup:** Es entwickelt **nur der Owner** an dieser App
(andere Personen *nutzen* sie nur über das Calandi-Tools-Portal — laden PDFs
hoch, entwickeln nicht). Darum ist das lokale `./bin/deploy.sh`-Gate der
Deploy-Schutz und **Branch Protection / PR-Pflicht ist NICHT nötig** —
Direkt-Push auf `main` ist hier in Ordnung. Disziplin: nie pushen ohne grünes
pytest (das macht `./bin/deploy.sh` automatisch).

> Branch-Protection (Feature-Branch → PR → Required Check `Test / pytest` →
> Merge → Deploy) wäre erst relevant, wenn ein **zweiter Entwickler** dazukommt.
> Dann schützt es davor, dass jemand ungetesteten Code direkt live pusht.
> Solange Solo: weglassen, ist nur Overhead.

Wichtig bleibt — *weil* andere die App nutzen — die Robustheits-Schichten
(Tests, Cross-Checks, Pattern-Fixtures, Restposten). Die fangen exotische
PDFs ab, nicht exotische Entwickler.

### Production-Acceptance — was MUSS eine ausgelieferte Excel haben

Jeder Übertrag der live ausgeliefert wird, MUSS die folgenden Kriterien
erfüllen (automatisch via `tests/test_end_to_end_robustness.py` geprüft —
einer pro Pattern):

| # | Kriterium | Automatisch geprüft durch |
|---|-----------|---------------------------|
| 1 | Excel wird gebaut (kein ValueError-Crash) | `_assert_excel_production_ready` |
| 2 | Konten aus den hochgeladenen JAs sind sichtbar | dito |
| 3 | Alle Gruppen-Sum-Zellen mit accounts = Formeln (`=SUM(...)` oder Kaskaden), niemals statische Werte | `test_alle_gruppen_sum_zellen_sind_formeln_kein_hardcoded_wert` |
| 4 | JÜ-Cross-Check Excel ↔ PDF centgenau (Diff < 1ct) in jeder JA-Spalte | Cross-Check in `build_excel` + Fragen-Sheet-Eintrag |
| 5 | Bei pdf_sum_gj != acc_sum: Restposten-Konto als Detail-Zeile sichtbar | `test_pattern_C_konten_unvollstaendig_restposten_ergaenzt` |
| 6 | BWA-JÜ direkter Verweis auf BWA-Endwert (keine Aggregat-Doppelzählung) | `test_pattern_D_bwa_only_mit_endwert` |

**Abgedeckte PDF-Format-Patterns** (alle in `test_end_to_end_robustness.py`):
- A: Vollständiger Kontennachweis (Tasteone-Style)
- B: DATEV-Rohergebnis-Format (Bilanzbericht, nur Gruppensummen)
- C: Konten unvollständig → Restposten ergänzt
- D: BWA-only mit Aggregat-Hierarchie (Vorläufiges Ergebnis als Endwert)
- E: EÜR §4 Abs 3 mit Hinzurechnungen/Kürzungen
- F: Multi-Year-Setup (3+ JAs, Cross-Year-Routing)

**Neuer PDF-Stil aufgetaucht?** → neues Pattern als Test-Fixture in
`test_end_to_end_robustness.py` ergänzen BEVOR der Fix deployed wird.
Sonst kommt's beim nächsten Mandant zurück.

### Prompt anpassen
1. `app/worker/prompts.py` ändern
2. `.venv/bin/python3 tests/fixtures/smoketest_claude.py` — verifizieren dass
   Claude noch valides JSON liefert
3. Test gegen ein echtes Jahresabschluss-PDF aus `M&A/` — Excel manuell öffnen
   und mit der PDF querprüfen
4. Commit + Deploy

### Bug-Report empfangen ("Spalte X sieht falsch aus")

Aus 5+2+3 Iterationen (April/Mai 2026) destillierter Standardablauf — verhindert
Hypothesen-vor-Daten-Iterationen (siehe `~/.claude/rules/pipeline-engineering.md`
Regel 5):

0. **REGELN RE-LESEN** — vor jeder Code-Änderung in `app/excel/` oder
   `app/worker/consolidate.py` zuerst den "Wichtige Regeln (nicht brechen)"-Block
   in dieser CLAUDE.md komplett durchgehen. **Disziplin reicht nicht** — bei
   20+ Regeln vergisst man unter Bug-Druck eine. Der PreToolUse-Hook
   `.claude/hooks/builder-rules-reminder.sh` druckt die Regeln automatisch vor
   jedem Edit; ignorieren = Selbst-Sabotage. Frage explizit: **"Welche dieser
   Regeln könnte mein geplanter Fix verletzen?"** Wenn unsicher: nochmal lesen.
1. **Daten ziehen** — User um die problematische Excel + die zugehörigen PDFs
   bitten. Ohne echte Daten nicht raten.
2. **Symptom in der Excel präzise dokumentieren** — welcher Konto-Wert weicht
   in welcher Spalte ab, was sagt das PDF dazu (im Originalformat). Tabelle
   pro betroffene Konten/Spalten.
3. **Eine konkrete Zahl manuell durch die Pipeline tracen** — PDF-Wert →
   Claude-JSON → consolidated → Excel-Zelle. Wo entsteht die Abweichung?
   Tipp: `tests/fixtures/smoketest_e2e.py` mit den realen PDFs laufen lassen
   und `debug_extractions.json` + `debug_consolidated.json` schreiben.
4. **Failing-Test schreiben (TDD)** — vor dem Fix einen Test in
   `tests/test_consolidate.py` oder `tests/test_excel_builder.py`, der den
   Bug reproduziert. Test rot machen, dann fixen, dann grün.
5. **Live-Smoketest mit ALLEN relevanten PDFs** — nicht nur dem
   problematischen Jahr. Multi-Jahr-Konstellationen können neue Bugs
   triggern (siehe Tasteone-Synthetic-Parent-Bug 2026-05).
6. **Commit + Deploy**: `git push` (oder `./bin/deploy.sh` mit Pre-Push-Gate).
   Der Webhook zieht den Stand auf den Server. Danach Live-Check auf
   `https://uebertrag.calandi-tools.de/health` (sollte `{"status":"ok"}`).
7. **POST-FIX-CHECK**: `pytest` muss grün sein UND der Regel-Enforcement-Test
   `test_alle_gruppen_sum_zellen_sind_formeln_kein_hardcoded_wert` muss bestanden
   sein. Wenn er rot ist: eine "Formel statt Wert"-Regel wurde verletzt — Fix
   überarbeiten (Restposten-Approach statt direkter Wert).

> [!warning]
> NIE Code anfassen bevor man die echten Roh-Daten gesehen hat. Jede
> Hypothese-vor-Daten-Iteration kostet einen User-Roundtrip.
>
> NIE eine "nicht brechen"-Regel umgehen weil der Fix dadurch schneller wäre.
> Wenn die Regel im Weg steht, ist entweder der Fix falsch oder die Regel muss
> diskutiert + geändert werden — aber NICHT stillschweigend gebrochen.

## Migration auf Calandi-Tools (Hetzner + Authentik)

Stand 2026-06-10, koordiniert mit Thomas & Leon (Calandi-Infra). Quelle:
`Downloads/2026-06-10-an-philipp-umbau-container-authentik.md`.

**Zielbild:** App läuft als Docker-Container auf dem Calandi-Hetzner hinter
nginx. nginx macht **Authentik Forward-Auth**; die App bekommt die Identität
über Header `X-Authentik-Username` / `-Email` / `-Groups`. Erreichbar unter
`https://uebertrag.calandi-tools.de` (Portal-Login, App-Auswahl). Deploy:
Push auf `main` → Webhook `…/hooks/deploy-uebertrag` → Container-Rebuild.

### Sicherheits-Stoppschilder (nicht überspringen)

> [!danger]
> **1. Login-Code erst entfernen, wenn die App NUR noch hinter Authentik
> hängt.** Solange sie offen auf Railway erreichbar ist, würde das Entfernen
> des Passwort-Gates sie ungeschützt ins Netz stellen.

> [!note]
> **2. ✅ ERLEDIGT (2026-06-10): Read-only Deploy-Key `calandi-server`
> eingetragen.** Das Repo war ohnehin **schon privat** (entgegen Thomas'
> Annahme, es sei noch öffentlich) — es gibt kein „auf privat schalten" mehr.
> Leon klont morgen über genau diesen read-only Key (per SSH); ein
> Public-Clone funktioniert nicht. Thomas wurde informiert.

> [!danger]
> **3. Supabase-Service-Key VOR der Secret-Übergabe rotieren.** Der alte Key
> wurde 2026-04-24 geleakt (Railway-Variables) und nie rotiert. Nicht den
> kompromittierten Key in die Hetzner-`.env` geben. **Runbook:**
> `docs/runbooks/2026-06-10-supabase-key-rotation.md` (Schritt-für-Schritt,
> Consumer-Liste, Option A = neuer `sb_secret_`-Key zero-downtime).

### Auth-Umbau (Login → Authentik)

- Eigenes Passwort-Gate / Session-Login ist entfernt; `APP_PASSWORD_HASH`
  und `SESSION_SECRET` entfallen vollständig (aus `config.py` raus). Die App
  braucht keines von beiden mehr — Frage „SESSION_SECRET doppelt?" damit erledigt.
- `require_auth` prüft den `X-Authentik-Username`-Header; `current_user()`
  liest Username/Email/Groups aus den `X-Authentik-*`-Headern.
- **Umsetzung:** Umgesetzt aus Thomas/Leons Spec
  (`Downloads/2026-06-11-an-philipp-auth-umbau-spec.md`), **am 11.06. in `main`
  gemerged** (Merge `7c6f40e`), 115 Tests grün. Sicher, weil Railway-Auto-Deploy
  aus ist und ein `main`-Push nur CI triggert, kein Live-Deploy — der Stand geht
  via Hetzner (hinter Authentik) live, sobald Thomas/Leon ihn gezogen + verifiziert
  haben. Damit ist Cutover-Schritt 5 codeseitig erledigt.

### Secrets server-seitig (Container-`.env`, NIE ins Repo)

`ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `PUBLIC_BASE_URL`
(`=https://uebertrag.calandi-tools.de`). Übergabe über **1Password** (Vault
„Calandi/Prozess-Uebertrag"), nicht per Mail/Chat-Text. (`SESSION_SECRET`
entfällt — der Auth-Patch entfernt es.)

### Cutover-Reihenfolge

```
✅ 0. Deploy-Key eintragen (erledigt 2026-06-10; Repo war schon privat)
   1. Supabase-Service-Key rotieren → neue Secrets via 1Password an Thomas/Leon
   2. Leon klont das Repo über den Deploy-Key (morgen)
   2b. ⚠️ VOR dem ersten Push: `"Bash(git push:*)"` aus dem `deny`-Block in
       `.claude/settings.json` entfernen — sonst blockt das Sicherheitsnetz
       jeden Push (bypassPermissions-Setup, wie Nylo). Erst dann kann der
       Webhook über einen Push getriggert werden.
   3. Hetzner-Container hochziehen, Webhook testen (Doku-Push als Erst-Test)
   4. uebertrag.calandi-tools.de hinter Authentik grün → /health + Test-Upload
✅ 5. Auth-Patch gemerged (11.06., Merge `7c6f40e`, Login raus). Sicher weil
      Auto-Deploy aus → main-Push triggert nur CI. Geht via Hetzner live, sobald
      Thomas/Leon den Stand gezogen + verifiziert haben.
   6. Railway abschalten
   7. AUFRÄUMEN: diese „Migration"-Sektion + Railway-Erwähnungen aus CLAUDE.md
      in docs/runbooks/ auslagern (3-Zeilen-Pointer behalten) → spart ~60
      Zeilen in der immer-geladenen CLAUDE.md. Erst NACH bestätigtem Cutover.
```

## Offene Punkte (TODO)

- **Supabase-Service-Key VOR Hetzner-Cutover rotieren** (geleakt 2026-04-24,
  Railway-Variables). Runbook: `docs/runbooks/2026-06-10-supabase-key-rotation.md`.
  Blockiert die Secret-Übergabe an Calandi-Infra. Erwägen: auch
  `ANTHROPIC_API_KEY` rotieren (war im selben Leak). `SESSION_SECRET` ist mit
  dem Auth-Patch entfernt — Rotation entfällt.
- **Rate-Limit ist In-Memory per Container** — bei Multi-Replica/Multi-Container
  funktioniert das nicht mehr. Aktuell OK weil Single-Container.
- **Phase 3 (festes GKV-Layout in §275-Reihenfolge)**: nicht umgesetzt, weil
  bei DATEV-Standard ohnehin die PDF-Reihenfolge der GKV-Reihenfolge
  entspricht. Bei exotischen STBs könnte ein erzwungenes GKV-Layout später
  helfen. Tracking via gkv_section ist schon drin, würde nur den Builder
  erweitern.

## Edge-Case-Verhalten (dokumentiert, kein Bug)

- **STB-Vorzeichen-Inversion in VJ-Spalten**: STBs drucken VJ-Konten oft mit
  umgekehrtem Vorzeichen. Eigenjahr ist authoritativ (`setdefault`), Mismatch
  wird still aufgelöst. **Grenze:** liegt ein Jahr **nur** als VJ vor (kein
  eigenes JA hochgeladen), kann sein Vorzeichen falsch sein — Workaround:
  fehlendes JA hochladen.
- **Spalten-Inversion + Hierarchie-Mix** (beide Tasteone 2026-05, gefixt) →
  siehe Regeln `_normalize_column_signs` bzw. `_build_section_to_tpl` oben.
- **Review-Screen** triggert nur wenn Claude echte `open_questions` liefert.
  Nach dem Umbau (PDF-Gliederung 1:1) passiert das selten. Wenn ein
  exotisches PDF reinkommt das Claude nicht einordnet → Review-UI zeigt alle
  Gruppen aus der konsolidierten Struktur als Dropdown.
- **Scan-PDFs** dauern 2-4 min und kosten ~0,40-0,60 €/PDF (Claude Vision).
  Nicht blockieren bei großen Scan-Deals, aber User warnen.

## Test-Suite

```bash
.venv/bin/pytest                      # 115 Tests (Stand 2026-06-11, Auth-Merge auf main)
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
