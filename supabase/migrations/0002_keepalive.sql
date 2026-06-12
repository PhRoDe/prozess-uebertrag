-- 0002_keepalive.sql
-- Keepalive / DB-Stabilitaetstest fuer calandi-tools.
-- Eigene Tabelle, unabhaengig von der App. Wird NICHT vom App-Cleanup angefasst
-- (App loescht nur gezielt aus `jobs` + eigenen Bucket-Pfaden).
-- Hinweis: identisches SQL laeuft auch im Memorandum-Projekt (separate DB).

create table if not exists public.keepalive (
    id           smallint primary key default 1,
    ping_count   bigint      not null default 0,
    last_ping_at timestamptz,
    last_source  text,
    constraint keepalive_single_row check (id = 1)
);

insert into public.keepalive (id) values (1)
on conflict (id) do nothing;

-- RLS an, keine Policy -> nur der service_role-Key (Server) darf ran.
alter table public.keepalive enable row level security;

-- Atomarer Ping: zaehlt +1, setzt Zeitstempel, gibt die Zeile zurueck.
-- Ein Aufruf testet damit Verbindung + Schreiben + Lesen in einem.
create or replace function public.keepalive_ping(p_source text default 'calandi-server')
returns public.keepalive
language sql
security definer
set search_path = public
as $$
    update public.keepalive
       set ping_count   = ping_count + 1,
           last_ping_at = now(),
           last_source  = p_source
     where id = 1
    returning *;
$$;

-- Funktion nur fuer den Server (service_role), nicht fuer anon/authenticated.
revoke all on function public.keepalive_ping(text) from public, anon, authenticated;
grant execute on function public.keepalive_ping(text) to service_role;
