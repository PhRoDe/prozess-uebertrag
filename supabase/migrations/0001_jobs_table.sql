-- 0001_jobs_table.sql
create table if not exists jobs (
    id                    uuid primary key default gen_random_uuid(),
    created_at            timestamptz not null default now(),
    status                text not null check (status in (
                              'uploaded', 'extracting', 'review_needed',
                              'finalizing', 'ready', 'failed', 'expired'
                          )),
    input_files           jsonb not null,
    extraction            jsonb,
    review_answers        jsonb,
    output_path           text,
    error_message         text,
    expires_at            timestamptz not null,
    -- Resume-Pattern (Fix 1A): Worker setzt processing_node beim Claim,
    -- beim Restart übernimmt der Watchdog stuck Jobs ohne processing_node
    processing_node       text,
    processing_started_at timestamptz
);

create index if not exists jobs_status_idx on jobs (status);
create index if not exists jobs_expires_at_idx on jobs (expires_at);
create index if not exists jobs_processing_idx on jobs (status, processing_node);

-- Cleanup function (ausgeführt via pg_cron)
create or replace function cleanup_expired_jobs()
returns void language plpgsql as $$
begin
    delete from jobs where expires_at < now();
end;
$$;

-- Watchdog: Jobs > 30 min in extracting/finalizing auf failed setzen
-- (nur Jobs die einen processing_node gesetzt hatten — aktive Worker haben Vorrang)
create or replace function watchdog_stale_jobs()
returns void language plpgsql as $$
begin
    update jobs
    set status = 'failed',
        error_message = 'Stale job: worker timeout',
        processing_node = null
    where status in ('extracting', 'finalizing')
      and processing_started_at < now() - interval '30 minutes';
end;
$$;

-- Resume-Helfer (Fix 1A): findet Jobs die beim App-Start resumed werden müssen
create or replace function jobs_pending_resume()
returns setof jobs language sql as $$
    select * from jobs
    where status in ('extracting', 'finalizing')
      and (processing_node is null
           or processing_started_at < now() - interval '5 minutes');
$$;

-- pg_cron Jobs (müssen vom Dashboard aktiviert werden nach Extension-Aktivierung)
-- select cron.schedule('cleanup-expired', '0 3 * * *', 'select cleanup_expired_jobs()');
-- select cron.schedule('watchdog-stale', '*/5 * * * *', 'select watchdog_stale_jobs()');
