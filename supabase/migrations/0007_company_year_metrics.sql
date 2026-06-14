-- Phase B2: kanonische Benchmarking-Kennzahlen pro Firma-Jahr.
-- MANUELL im Supabase-SQL-Editor ausführen, BEVOR der Code deployt.
--
-- WICHTIG: source_job_id ON DELETE SET NULL (NICHT cascade) — Jobs sind
-- ephemer (cleanup_expired_jobs löscht sie), die Benchmark-Kennzahlen müssen
-- aber DAUERHAFT bleiben. company_id ON DELETE CASCADE (Firma weg → Kennzahlen
-- weg). Alle Tabellen RLS an (nur service_role).

-- Formel-Versionierung (Council: Reproduzierbarkeit). metrics_version in
-- company_year_metrics referenziert die hier dokumentierte Formel-Generation.
create table if not exists metric_definitions (
    version  int not null,
    metric   text not null,
    formula  text not null,
    basis    text,                       -- Bezugsgröße bei Quoten
    primary key (version, metric)
);
alter table metric_definitions enable row level security;

create table if not exists company_year_metrics (
    company_id     uuid not null references companies(id) on delete cascade,
    fiscal_year    int not null,
    data_source    text not null default 'ja',     -- ja | bwa | euer | susa
    source_job_id  uuid references jobs(id) on delete set null,
    -- Absolutwerte (sign-korrigiert)
    umsatz                 numeric,
    gesamtleistung         numeric,
    materialaufwand        numeric,
    rohertrag              numeric,
    personalaufwand        numeric,
    abschreibungen         numeric,
    sonst_betr_aufw        numeric,
    betriebsergebnis       numeric,
    finanzergebnis         numeric,
    neutrales_ergebnis     numeric,
    steuern                numeric,
    jue                    numeric,
    -- Quoten (Basis Gesamtleistung)
    personalaufwandsquote  numeric,
    rohertragsmarge        numeric,
    ebit_marge             numeric,
    jue_marge              numeric,
    -- Datenqualität
    completeness_score     numeric,
    restposten_anteil      numeric,
    has_open_questions     boolean,
    metrics_version        int not null,
    recomputed_at          timestamptz not null default now(),
    primary key (company_id, fiscal_year, data_source)
);
create index if not exists cym_company_idx on company_year_metrics (company_id);
create index if not exists cym_year_idx on company_year_metrics (fiscal_year);
alter table company_year_metrics enable row level security;

-- v1-Formeln dokumentieren (Quelle: app/metrics.py, METRICS_VERSION=1).
insert into metric_definitions (version, metric, formula, basis) values
  (1, 'gesamtleistung', 'umsatz + bestandsveraenderung + aktivierte_eigenleistungen', null),
  (1, 'rohertrag', 'gesamtleistung - materialaufwand', null),
  (1, 'betriebsergebnis', 'rohertrag + sonst_betr_ertraege - personalaufwand - abschreibungen - sonst_betr_aufw', null),
  (1, 'finanzergebnis', 'finanzertraege - zinsaufwand', null),
  (1, 'neutrales_ergebnis', 'jue + steuern - betriebsergebnis - finanzergebnis (Residuum)', null),
  (1, 'jue', 'PDF-Anker (pdf_jahresueberschuss) falls vorhanden, sonst ergebnis_vor_steuern - steuern', null),
  (1, 'personalaufwandsquote', 'personalaufwand / gesamtleistung', 'gesamtleistung'),
  (1, 'rohertragsmarge', 'rohertrag / gesamtleistung', 'gesamtleistung'),
  (1, 'ebit_marge', 'betriebsergebnis / gesamtleistung', 'gesamtleistung'),
  (1, 'jue_marge', 'jue / gesamtleistung', 'gesamtleistung')
on conflict (version, metric) do update
  set formula = excluded.formula, basis = excluded.basis;
