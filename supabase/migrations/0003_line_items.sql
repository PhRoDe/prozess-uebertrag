-- Phase 2: relationaler Konten-Stand pro Job.
--
-- `consolidated` (JSONB in jobs.extraction) bleibt die Build-Quelle fuer das
-- Excel. Diese Tabellen sind die abgeleitete, querybare Schicht fuer Abgleich,
-- Audit und (spaeter) Editieren — geschrieben beim Extrahieren, gepflegt parallel.
--
-- Beide Tabellen haengen per FK an jobs(id) ON DELETE CASCADE: das App-Cleanup
-- loescht nur aus `jobs` (delete ... eq(id) + pg_cron cleanup_expired_jobs),
-- die line_items werden dadurch automatisch mit-geloescht. Keine eigene
-- Loesch-Logik noetig — und `keepalive` bleibt davon unberuehrt.

-- Einzelne Konten (eine Zeile pro Konto x Spalte).
create table if not exists line_items (
    id            uuid primary key default gen_random_uuid(),
    job_id        uuid not null references jobs(id) on delete cascade,
    created_at    timestamptz not null default now(),
    source_type   text,                          -- 'ja' | 'bwa' | 'susa'
    col_idx       int,                           -- Spalten-Index in consolidated
    column_label  text,                          -- z.B. '2024', 'Susa Dez 2025'
    group_name    text,
    gkv_section   text,
    konto_nr      text,
    bezeichnung   text,
    betrag        numeric,
    confidence    text,
    is_restposten boolean not null default false
);

create index if not exists line_items_job_id_idx on line_items (job_id);

-- Gruppen-Ebene pro Spalte: gedruckte Gruppensumme (pdf_sum) vs Konten-Summe.
-- Traegt den Vollstaendigkeits-Abgleich (Diff/complete) querybar.
create table if not exists line_item_groups (
    id            uuid primary key default gen_random_uuid(),
    job_id        uuid not null references jobs(id) on delete cascade,
    created_at    timestamptz not null default now(),
    col_idx       int,
    column_label  text,
    group_name    text,
    gkv_section   text,
    printed_sum   numeric,                        -- pdf_sum_gj/_vj aus dem PDF
    acc_sum       numeric                         -- Summe der erfassten Konten
);

create index if not exists line_item_groups_job_id_idx on line_item_groups (job_id);

-- Vollstaendigkeits-View: zeigt pro Job die Gruppen/Spalten, deren Konten-Summe
-- von der gedruckten Summe abweicht (> 1 ct). Unabhaengig vom Python-Builder —
-- eine zweite, in SQL pruefbare Sicht auf "sind alle Konten da?".
--
-- security_invoker=on: die View wendet die RLS der zugreifenden Rolle auf die
-- Basis-Tabelle an (statt mit Owner-Rechten zu laufen) — sonst koennte anon die
-- View trotz Tabellen-RLS lesen. Plus revoke fuer anon/authenticated (wie bei
-- keepalive). Nur service_role (App) liest sie. (Code-Review-Finding 2026-06.)
create or replace view v_job_completeness
with (security_invoker = on) as
select
    job_id,
    col_idx,
    column_label,
    group_name,
    gkv_section,
    printed_sum,
    acc_sum,
    round(coalesce(printed_sum, 0) - coalesce(acc_sum, 0), 2) as diff
from line_item_groups
where printed_sum is not null
  and abs(coalesce(printed_sum, 0) - coalesce(acc_sum, 0)) > 0.01;

revoke all on v_job_completeness from anon, authenticated;

-- RLS aktivieren (wie keepalive): ohne Policies greift nur der service_role-Key,
-- den die App nutzt — anon/authenticated kommen nicht dran. Defense-in-depth
-- fuer die Finanzdaten; aendert nichts am App-Zugriff (service_role bypassed RLS).
alter table line_items enable row level security;
alter table line_item_groups enable row level security;
