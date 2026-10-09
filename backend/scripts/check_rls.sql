-- Fails if any table in public or engine has row-level security disabled, or
-- if the Data API roles can reach the engine schema at all.
do $$
declare
  missing text;
begin
  select string_agg(format('%I.%I', schemaname, tablename), ', ')
    into missing
    from pg_tables
   where schemaname in ('public', 'engine')
     and not rowsecurity;

  if missing is not null then
    raise exception 'Row-level security is disabled on: %', missing;
  end if;

  if has_schema_privilege('anon', 'engine', 'USAGE')
     or has_schema_privilege('authenticated', 'engine', 'USAGE') then
    raise exception 'anon or authenticated has USAGE on schema engine';
  end if;
end $$;
