#!/bin/bash
# PreToolUse-Hook: Erinnert an die "nicht brechen"-Regeln bevor app/excel/builder.py
# oder app/worker/consolidate.py editiert werden. Verhindert nicht — informiert nur.
# Hintergrund: 2026-05-13 wurde die Formel-Pflicht-Regel verletzt; Disziplin allein
# reicht nicht bei langer Regel-Liste.

# stdin liefert das Tool-Input als JSON (via Claude Code Hook-Spec)
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

# Nur feuern wenn die kritischen Dateien editiert werden
if ! echo "$FILE_PATH" | grep -qE '(app/excel/builder|app/worker/consolidate)\.py$'; then
  exit 0
fi

REMINDER=$(cat <<'EOF'
⚠️ REGEL-CHECK vor Edit von Excel-Builder/Consolidate (CLAUDE.md "nicht brechen"-Block):

1. Alle Excel-Zwischensummen MÜSSEN Formeln sein (=SUM(...) oder Kaskaden).
   Niemals hardcoded Werte in Gruppen-Sum-Zellen. Bei pdf_sum_gj != acc_sum:
   Restposten-Konto ergänzen, nicht column_sum direkt schreiben.

2. Werte behalten ihr Vorzeichen wie im PDF (Claude normalisiert nicht).
   sign_convention pro Spalte, Mehrheits-Vote bei Outlier-Inversion.

3. gkv_section ist authoritativ über type für die JÜ-Formel-Klassifikation.

4. Bestandsveränderung: Position-Name authoritativ (Erhöhung +|wert|, Verminderung -|wert|).

5. JSON-Roundtrip-sicher: int-Keys → String in JSONB. _coerce_int_keys verwenden.

6. Build-Time-Plausibilitäts-Anker: Excel-JÜ ↔ PDF-JÜ Diff > 1ct als WARNING ins
   Fragen-Sheet (nicht ValueError hard-fail — User braucht die Excel zum Prüfen).

7. EÜR Hinzurechnungen/Kürzungen: type-spezifische Sub-Group-Iteration in JÜ-Formel.

8. Susa-Filterung: Klassen 0/1/9 ausgeschlossen (Bilanz, Saldenvorträge).

9. BWA-Aggregat-Doppelzählung verhindern: column_sum direkt + Aggregat-Skip wenn
   andere Spalte Konten-Daten hat.

10. Synthetic-Parent < real-Sub bei Section-Routing (rank 0 > 1 > 2).

VOR DEM EDIT: Welche dieser Regeln könnte mein geplanter Fix verletzen?
NACH DEM EDIT: pytest laufen lassen + die Regel-Tests (test_alle_gruppen_sum_zellen_sind_formeln_*) checken.
EOF
)

# additionalContext als JSON-Output (wird Claude angezeigt vor dem Edit)
jq -n --arg ctx "$REMINDER" '{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": $ctx
  }
}'
exit 0
