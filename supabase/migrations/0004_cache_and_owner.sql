-- Phase 4: PDF-Extraktions-Cache + created_by-Scoping.
--
-- MANUELL im Supabase-SQL-Editor ausführen, BEVOR der Code deployt, der sie
-- nutzt (keine Auto-Migration). created_by ist nullable → Bestandsjobs vor der
-- Migration bleiben gültig, kein Container-Crash beim Start.

-- Wer den Job angelegt hat (X-Authentik-Username). Nullable: Legacy-Jobs ohne
-- Wert bleiben für alle authentifizierten User zugänglich (kein Lockout).
-- RLS ist mit dem service_role-Key wirkungslos → das Owner-Scoping passiert in
-- der App (job_owner_ok), nicht über RLS.
alter table jobs add column if not exists created_by text;

-- Inhalts-adressierter Cache der rohen _extract_pdf-Ausgabe je (PDF-Hash ×
-- Modell). Spart teure Claude-Calls bei Re-Uploads / Re-Runs (v.a. Scan-PDFs,
-- die 2-4 min dauern und ~0,40-0,60 € kosten). Kein job_id-FK: der Cache wird
-- über Jobs hinweg geteilt und NICHT im App-Cleanup gelöscht (eigene Retention
-- via created_at unten). Enthält Finanzdaten → wie line_items/keepalive: RLS an,
-- nur service_role (App) liest.
create table if not exists pdf_extractions (
    pdf_hash     text not null,
    model        text not null,
    extractions  jsonb not null,
    created_at   timestamptz not null default now(),
    primary key (pdf_hash, model)
);

alter table pdf_extractions enable row level security;

-- Optionale Retention: gecachte Finanzdaten nicht ewig halten. pg_cron räumt
-- Einträge älter als 30 Tage weg (gezieltes DELETE, kein truncate — keepalive
-- bleibt unberührt). Best-effort: nur wenn pg_cron verfügbar ist.
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.schedule(
      'cleanup_pdf_extractions',
      '0 3 * * *',
      $cron$delete from public.pdf_extractions where created_at < now() - interval '30 days'$cron$
    );
  end if;
end $$;
