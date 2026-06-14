-- Security-Audit (2026-06-14): jobs-RLS + Function-Grants nachziehen.
-- MANUELL im Supabase-SQL-Editor ausführen. KEIN Code-Deploy nötig — die App
-- nutzt den service_role-Key (BYPASSRLS), ist also unberührt; pg_cron läuft als
-- postgres (bypassed RLS ebenfalls). Geschlossen wird nur der anon/authenticated-
-- Zugriff über den öffentlichen PostgREST-Endpoint.

-- Finding 1: jobs (Finanzdaten in extraction/review_answers) hatte kein RLS.
-- RLS an, KEINE Policies → deny-all für anon/authenticated (wie keepalive/
-- line_items/companies). service_role + pg_cron bleiben funktionsfähig.
alter table public.jobs enable row level security;

-- Finding 2: public-Functions waren per Default an PUBLIC EXECUTE-grantet →
-- über /rest/v1/rpc/... anon-aufrufbar. jobs_pending_resume gibt ALLE Jobs
-- zurück (Leak); cleanup/watchdog mutieren jobs (DoS). Wie bei keepalive_ping:
-- von public/anon/authenticated entziehen.
revoke all on function public.jobs_pending_resume() from public, anon, authenticated;
revoke all on function public.cleanup_expired_jobs() from public, anon, authenticated;
revoke all on function public.watchdog_stale_jobs() from public, anon, authenticated;

-- jobs_pending_resume ruft die App via rpc mit service_role auf → braucht grant.
grant execute on function public.jobs_pending_resume() to service_role;
-- cleanup_expired_jobs / watchdog_stale_jobs laufen über pg_cron (postgres) —
-- kein anon/service_role-grant nötig.
