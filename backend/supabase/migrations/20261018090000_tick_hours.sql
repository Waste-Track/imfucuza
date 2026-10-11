-- An optional ticking window, for a staging service on Render's free plan:
-- a service ticked around the clock never sleeps and uses up the workspace's
-- free hours. Set the Vault secret engine_tick_hours to "7-22" to tick only
-- from 07:00 to 21:59 UTC, which is Ghana time. Without it, or with any other
-- value, the queue is ticked around the clock, so timers never stop silently.

create or replace function engine.tick() returns bigint
language plpgsql
set search_path = ''
as $$
declare
  tick_url text;
  secret text;
  hours text;
  now_hour int := extract(hour from now() at time zone 'UTC');
begin
  select decrypted_secret into tick_url
    from vault.decrypted_secrets where name = 'engine_tick_url';
  select decrypted_secret into secret
    from vault.decrypted_secrets where name = 'engine_internal_secret';
  if tick_url is null or secret is null then
    return null;
  end if;
  select decrypted_secret into hours
    from vault.decrypted_secrets where name = 'engine_tick_hours';
  if hours ~ '^\d{1,2}-\d{1,2}$'
     and not (now_hour >= split_part(hours, '-', 1)::int
              and now_hour < split_part(hours, '-', 2)::int) then
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
