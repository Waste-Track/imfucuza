-- Fails if any table in an API-exposed schema has row-level security disabled.
do $$
declare
  missing text;
begin
  select string_agg(format('%I.%I', schemaname, tablename), ', ')
    into missing
    from pg_tables
   where schemaname = 'public'
     and not rowsecurity;

  if missing is not null then
    raise exception 'Row-level security is disabled on: %', missing;
  end if;
end $$;
