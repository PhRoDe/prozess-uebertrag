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
| `app/worker/prompts.py` | Extraktions-Prompts für Claude (HGB-GuV+EÜR / BWA / Susa) — Quelle der Wahrheit für "wie soll das JSON aussehen" |
| `app/worker/claude_client.py` | anthropic SDK Wrapper, doc_type-aware (`extract_text_pdf(doc_type=...)`), 429-Retry, `<pdf_content>`-Delimiter |
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
- **Bestandsveränderung universal** (`_normalize_bestand_value` in
  `consolidate.py`): Bei `gkv_section="bestandsveraenderung"` ist der
  Position-Name authoritativ — "Erhöhung" → `+|wert|`, "Verminderung" →
  `-|wert|`. Eingehendes Vorzeichen wird ignoriert. Damit funktioniert die
  Formel egal welche STB-Vorzeichen-Konvention das PDF nutzt. Die JÜ-Formel
  addiert die Gruppe (das Vorzeichen entscheidet die Wirkung).
- **JSON-Roundtrip-sicher**: int-Keys in `values: {col_idx: ...}` werden beim
  Speichern in Postgres JSONB zu Strings. `_coerce_int_keys` im Builder casted
  sie beim Eintreten zurück. Niemals direkt mit int auf das Dict zugreifen
  ohne vorher zu coercen.
- **Build-Time-Plausibilitäts-Anker**: Excel-Endwert ↔ PDF-Endwert wird
  centgenau geprüft. Diff > 1 ct → `ValueError` aus `build_excel`, Job geht
  auf FAILED. Endwert-Begriff ist dynamisch via `endwert_label`
  ("Jahresüberschuss" bei HGB / "Steuerlicher Gewinn nach §4 Abs 3 EStG"
  bei EÜR). Bei Susa kein Cross-Check (kein Endwert in der Susa).
- **EÜR Hinzurechnungen + Kürzungen**: Hinzurechnungen → `type="ertrag"`
  (addiert), Kürzungen → `type="aufwand"` (subtrahiert). Mit
  `expenses_positive` ergibt das den korrekten Steuerlichen Gewinn.
  Im Builder iteriert `_endwert_groups()` bei Top-Level-Gruppen ohne
  eigene accounts (= synthetic Parent wie "D. STEUERLICHE KORREKTUREN")
  auf Sub-Group-Ebene, weil mixed-type Subs nicht durch einen Parent-
  Wrapper repräsentiert werden können.
- **Susa-Filterung**: Klassen 0/1/9 (Bilanz, Saldenvorträge) werden in
  `SUSA_PROMPT` explizit ausgeschlossen — sonst landen Pkw, Bank,
  Privatentnahmen in der GuV-Excel.
- **BWA-Aggregat-Doppelzählung verhindert**: BWA-Aggregat-Gruppen ohne
  eigene Konten (Personalkosten, Raumkosten etc., die JA-Gruppen
  zusammenfassen) werden in der JÜ-Formel übersprungen wenn die Spalte
  bereits Konten-Daten in JA-Top-Level-Gruppen hat. Heuristik in
  `build_excel`: `col_has_account_data and not g.get("accounts")` → skip.
- **Sign-Outlier-Normalisierung pro Spalte** (`_normalize_column_signs`
  in `consolidate.py`, seit 2026-05-10): Wenn Claude für **eine einzelne
  Spalte** die Vorzeichen-Konvention invertiert (alle Aufwand-Konten
  negativ statt positiv, alle Skonti positiv statt negativ — komplette
  Spalten-Spiegelung), wird das per Mehrheits-Vote über die anderen
  Spalten erkannt und stillschweigend korrigiert (Werte ×−1). JÜ bleibt
  mathematisch korrekt, visuelle Konsistenz hergestellt. Greift nur bei
  klarer Mehrheit (>50%, ≥2 Spalten Konsens). Auslöser-Pattern: Suffix-
  Minus an "Übertrag"-Zwischensummen mehrseitiger Tabellen verleitet
  Claude zur Spalten-Inversion (Tasteone-2022-Bug).
- **Synthetic-Parent darf bei Section-Routing nicht gegen Subs gewinnen**
  (`_build_section_to_tpl` in `consolidate.py`, seit 2026-05-10): Wenn
  ältere JAs flach geliefert werden ("Aufwendungen für RHB" als Top-
  Level) und das Template-Doc hierarchisch ("4. a) Aufwendungen für RHB"
  unter "4. Materialaufwand"), wird der Parent von `_insert_missing_parents`
  synthetisch erzeugt. Beim Section-basierten Routing ältere Konten:
  Sub > synthetic Top, sonst Doppelzählung weil neuere Docs die Sub
  exact-name treffen und ältere via Section in den synthetic Parent
  laufen. Marker `_synthetic_parent=True` wird in `_insert_missing_parents`
  gesetzt und in `_build_section_to_tpl` (Rang 2 = niedrigste Priorität)
  ausgewertet.
- **Stille Auflösung von Audit-Mismatches**:
  - `previous_year_mismatch`: Eigenjahres-Wert ist authoritativ (`setdefault`
    schreibt VJ-Wert nur wenn Spalte leer). Kein Fragen-Sheet-Eintrag.
  - `group_sum_mismatch`: Konten-Summe ist authoritativ über `pdf_sum_gj`
    (Claude erfindet den manchmal via Übertrag-Doppelzählung). Kein Eintrag.
  - `unmatched_account` bleibt im Fragen-Sheet — echte User-Entscheidung.
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
- **STB-Vorzeichen-Inversionen in VJ-Spalten**: STBs drucken VJ-Konten oft mit
  umgekehrtem Vorzeichen vs. der eigenen Spalte des entsprechenden Jahres.
  Mit der aktuellen Logik ist der **Eigenjahres-Wert authoritativ** —
  VJ-Werte werden nur per `setdefault` ergänzt, der Mismatch wird
  stillschweigend aufgelöst. Praxis: Wenn ein Jahr **nur** als VJ vorliegt
  (kein eigenes JA hochgeladen), kann das Vorzeichen für dieses Jahr
  falsch sein. Einziger Workaround: das fehlende JA hochladen.
- **Phase 3 (festes GKV-Layout in §275-Reihenfolge)**: nicht umgesetzt, weil
  bei DATEV-Standard ohnehin die PDF-Reihenfolge der GKV-Reihenfolge
  entspricht. Bei exotischen STBs könnte ein erzwungenes GKV-Layout später
  helfen. Tracking via gkv_section ist schon drin, würde nur den Builder
  erweitern.
- **Tasteone-Sign-Bug (2026-05-10, gefixt)**: User meldete invertierte
  2022er Spalte (Aufwand negativ, Skonti positiv) bei sonst korrekten
  Jahren. Root Cause: Claude-Halluzination, vermutlich getriggert durch
  Suffix-Minus an "Übertrag"-Zwischensummen mehrseitiger Tabellen.
  Sekundärer Bug: Doppelzählung wenn ältere JAs flache Hierarchie
  liefern und neuere Docs hierarchisch (Synthetic-Parent gewann via
  Section-Routing gegen reale Sub). Beide Fixes (Sign-Outlier-
  Normalisierung + Synthetic-Parent-De-Priorisierung) live ab Commit
  `6fce589`. Voller 5-JA-Smoketest (2020-2024) am 2026-05-10 verifiziert
  centgenaue JÜ-Übereinstimmung über alle 6 Spalten.

## Test-Suite

```bash
.venv/bin/pytest                      # 106 Tests (Stand 2026-05-10)
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
