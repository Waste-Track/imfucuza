-- Supabase Cron drives the Engine's job queue by calling POST /internal/tick.
-- The URL and secret live in Vault, set once per project (see the README).
-- Until both are set the schedule runs but sends nothing, as locally and in CI.

create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net with schema extensions;

create function engine.tick() returns bigint
language plpgsql
set search_path = ''
as $$
declare
  tick_url text;
  secret text;
begin
  select decrypted_secret into tick_url
    from vault.decrypted_secrets where name = 'engine_tick_url';
  select decrypted_secret into secret
    from vault.decrypted_secrets where name = 'engine_internal_secret';
  if tick_url is null or secret is null then
    return null;
  end if;
  -- A tick can work for about 40 s (a 10 s budget, then up to 30 s on the last job).
  return net.http_post(
    url := tick_url,
    headers := jsonb_build_object(
      'content-type', 'application/json', 'x-internal-secret', secret
    ),
    timeout_milliseconds := 60000
  );
end
$$;

revoke execute on function engine.tick() from public;

-- Offer SMS are sent by jobs, and app riders have 90 s to answer an offer, so
-- the queue can't wait a whole minute.
select cron.schedule('engine-tick', '10 seconds', 'select engine.tick()');

-- A run every 10 s adds over 8,000 history rows a day.
select cron.schedule(
  'engine-cron-history',
  '17 3 * * *',
  $$delete from cron.job_run_details where end_time < now() - interval '7 days'$$
);
