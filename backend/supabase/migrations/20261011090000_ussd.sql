-- USSD sessions: each keypress is a separate request, so the menu position
-- and anything chosen so far live here between requests.
create table engine.ussd_sessions (
  provider text not null,
  session_id text not null,
  -- Bound on the first request. A later request from another number is refused.
  msisdn text not null,
  user_id uuid references engine.users (id),
  screen text not null,
  context jsonb not null default '{}',
  -- For providers that count steps: a resent step gets the same reply again
  -- instead of being applied twice.
  last_step integer,
  last_reply text,
  last_end boolean,
  started_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (provider, session_id)
);

create index ussd_sessions_updated_idx on engine.ussd_sessions (updated_at);

alter table engine.ussd_sessions enable row level security;

-- Where a feature-phone rider said they arrived: the pickup's own location.
alter table engine.rider_locations drop constraint rider_locations_source_check;
alter table engine.rider_locations add constraint rider_locations_source_check
  check (source in ('gps', 'landmark', 'zone', 'arrival'));
