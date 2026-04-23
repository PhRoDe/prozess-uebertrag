# Prozess-Übertrag

Interne Calandi-Web-App: Jahresabschluss-PDFs → verformelte Excel.

## Entwicklung

- `uv sync --extra dev` oder `pip install -e ".[dev]"`
- `uvicorn app.main:app --reload`
- Tests: `pytest`

## Deployment

- Railway: `git push` → Auto-Deploy
- Supabase: Migrations in `supabase/migrations/`

## Wichtige Regeln

- Extraktions-Logik lebt in `app/worker/`, KEINE HTTP-Imports dort
- Excel-Logik lebt in `app/excel/`, KEINE Netzwerk-Calls dort
- Alle Excel-Zwischensummen MÜSSEN Formeln sein (`=SUM(...)`), nie hardcoded
- Siehe `docs/specs/2026-04-23-prozess-uebertrag-design.md` Section 4
