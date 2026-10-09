-- Engine core schema. Tables live in `engine`, which the Supabase Data API
-- does not expose: clients reach this data only through the Engine API.

create schema engine;
revoke all on schema engine from public, anon, authenticated;

-- Shared guard for append-only tables.
create function engine.forbid_mutation() returns trigger
language plpgsql as $$
begin
  raise exception '% on %.% is not allowed: the table is append-only',
    tg_op, tg_table_schema, tg_table_name
    using errcode = 'insufficient_privilege';
end $$;

-- People -------------------------------------------------------------------

create table engine.users (
  id uuid primary key default gen_random_uuid(),
  -- Null for people who only use USSD/SMS and never sign in to a PWA.
  auth_user_id uuid unique,
  role text not null check (role in ('household', 'rider', 'supervisor', 'admin')),
  phone_e164 text not null unique check (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  name text,
  consent_version text,
  consent_at timestamptz,
  consent_channel text check (consent_channel in ('pwa', 'ussd', 'sms', 'paper')),
  status text not null default 'active' check (status in ('active', 'suspended', 'deleted')),
  created_at timestamptz not null default now()
);

create table engine.zones (
  id smallint generated always as identity primary key,
  name text not null unique,
  lat double precision not null,
  lng double precision not null,
  radius_m integer not null check (radius_m > 0),
  ussd_index smallint not null unique check (ussd_index between 1 and 99)
);

create table engine.landmarks (
  id integer generated always as identity primary key,
  zone_id smallint not null references engine.zones (id),
  name text not null,
  lat double precision not null,
  lng double precision not null,
  ussd_index smallint not null check (ussd_index between 1 and 99),
  unique (zone_id, ussd_index)
);

create table engine.households (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references engine.users (id),
  zone_id smallint references engine.zones (id),
  landmark_id integer references engine.landmarks (id),
  address_text text,
  lat double precision,
  lng double precision,
  plastic_eligible boolean not null default true,
  created_at timestamptz not null default now()
);

create table engine.riders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references engine.users (id),
  channel text not null check (channel in ('pwa', 'ussd')),
  on_duty boolean not null default false,
  duty_since timestamptz,
  max_active_jobs smallint not null default 1 check (max_active_jobs > 0),
  unreachable_until timestamptz,
  missed_offers smallint not null default 0,
  payout_msisdn text check (payout_msisdn ~ '^\+[1-9][0-9]{7,14}$'),
  payout_msisdn_changed_at timestamptz,
  created_at timestamptz not null default now()
);

-- Pickups ------------------------------------------------------------------

create table engine.pickup_requests (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references engine.households (id),
  offering text not null check (offering in ('refuse', 'plastic')),
  -- Allowed transitions are enforced by the Engine's state machine.
  status text not null check (status in (
    'awaiting_payment', 'pending_dispatch', 'offered', 'assigned', 'arrived',
    'location_issue', 'verifying', 'rider_review', 'awaiting_pin', 'disputed',
    'completed', 'rejected', 'unconfirmed', 'cancelled', 'expired', 'failed', 'refunded'
  )),
  version integer not null default 0,
  declared_type text,
  fee_pesewas integer check (fee_pesewas >= 0),
  lat double precision,
  lng double precision,
  zone_id smallint references engine.zones (id),
  assigned_rider_id uuid references engine.riders (id),
  verification_outcome text not null default 'none' check (verification_outcome in (
    'none', 'pending', 'accepted', 'deferred', 'rejected', 'manual_review'
  )),
  weight_g integer check (weight_g >= 0),
  failure_reason text,
  requested_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  check ((offering = 'refuse') = (fee_pesewas is not null))
);

create index pickup_requests_status_idx on engine.pickup_requests (status);
create index pickup_requests_rider_status_idx on engine.pickup_requests (assigned_rider_id, status);
create index pickup_requests_household_idx on engine.pickup_requests (household_id, requested_at);
create index pickup_requests_zone_idx on engine.pickup_requests (zone_id, requested_at);

-- Event log: audit trail and the source for monitoring signals S1 to S15.
create table engine.events (
  id bigint generated always as identity primary key,
  name text not null,
  occurred_at timestamptz not null default now(),
  actor_type text not null check (actor_type in (
    'household', 'rider', 'supervisor', 'admin', 'system', 'provider'
  )),
  actor_id uuid,
  pickup_id uuid references engine.pickup_requests (id),
  payload jsonb not null default '{}'
);

create index events_name_idx on engine.events (name, occurred_at);
create index events_pickup_idx on engine.events (pickup_id);

create trigger events_append_only before update or delete on engine.events
  for each row execute function engine.forbid_mutation();
create trigger events_no_truncate before truncate on engine.events
  for each statement execute function engine.forbid_mutation();

-- Background jobs, drained by POST /internal/tick ----------------------------

create table engine.jobs (
  id bigint generated always as identity primary key,
  kind text not null,
  payload jsonb not null default '{}',
  run_at timestamptz not null default now(),
  attempts integer not null default 0,
  max_attempts integer not null default 5 check (max_attempts > 0),
  last_error text,
  -- Set while a tick works on the job. A crashed tick's lease simply expires.
  locked_until timestamptz,
  done_at timestamptz,
  failed_at timestamptz,
  dedupe_key text unique,
  created_at timestamptz not null default now()
);

create index jobs_due_idx on engine.jobs (run_at) where done_at is null and failed_at is null;

-- Ledger -------------------------------------------------------------------
-- Double-entry and append-only. Money is integer pesewas (GHS), points are
-- integer PTS. An account's balance grows when an entry is on its normal side.

create table engine.ledger_accounts (
  id bigint generated always as identity primary key,
  code text not null unique,
  unit text not null check (unit in ('GHS', 'PTS')),
  normal_side text not null check (normal_side in ('debit', 'credit')),
  allow_negative boolean not null default false,
  balance bigint not null default 0,
  created_at timestamptz not null default now(),
  constraint ledger_accounts_balance_non_negative check (allow_negative or balance >= 0)
);

create table engine.ledger_transactions (
  id bigint generated always as identity primary key,
  kind text not null,
  unit text not null check (unit in ('GHS', 'PTS')),
  idempotency_key text not null unique,
  pickup_id uuid references engine.pickup_requests (id),
  reverses_txn_id bigint references engine.ledger_transactions (id),
  memo text,
  -- The database transaction that created this row. Entries may only be added
  -- by that same transaction, so a committed posting can never grow.
  created_xact xid8 not null default pg_current_xact_id(),
  created_at timestamptz not null default now()
);

create table engine.ledger_entries (
  id bigint generated always as identity primary key,
  txn_id bigint not null references engine.ledger_transactions (id),
  account_id bigint not null references engine.ledger_accounts (id),
  side text not null check (side in ('debit', 'credit')),
  amount bigint not null check (amount > 0),
  created_at timestamptz not null default now()
);

create index ledger_entries_txn_idx on engine.ledger_entries (txn_id);
create index ledger_entries_account_idx on engine.ledger_entries (account_id, created_at);
create index ledger_transactions_pickup_idx on engine.ledger_transactions (pickup_id);
-- A pickup's escrow is either released or refunded, once.
create unique index ledger_transactions_one_settlement_per_pickup
  on engine.ledger_transactions (pickup_id) where kind in ('release', 'refund_due');

-- Each entry moves its account's balance. The balance CHECK rejects an
-- overdraw, and the row lock taken here serialises concurrent postings.
create function engine.ledger_apply_entry() returns trigger
language plpgsql as $$
declare
  txn record;
  account_unit text;
begin
  select unit, created_xact into txn from engine.ledger_transactions where id = new.txn_id;
  if txn.created_xact <> pg_current_xact_id() then
    raise exception 'ledger transaction % is already committed and cannot take more entries',
      new.txn_id using errcode = 'insufficient_privilege';
  end if;
  select unit into account_unit from engine.ledger_accounts where id = new.account_id;
  if account_unit is distinct from txn.unit then
    raise exception 'ledger entry unit % does not match transaction unit %', account_unit, txn.unit
      using errcode = 'check_violation';
  end if;

  perform set_config('engine.applying_ledger_entry', 'on', true);
  update engine.ledger_accounts
     set balance = balance + case when normal_side = new.side then new.amount else -new.amount end
   where id = new.account_id;
  perform set_config('engine.applying_ledger_entry', 'off', true);
  return new;
end $$;

create trigger ledger_entries_apply after insert on engine.ledger_entries
  for each row execute function engine.ledger_apply_entry();

-- Checked at commit, once all of a transaction's entries exist.
create function engine.ledger_check_balanced() returns trigger
language plpgsql as $$
declare
  txn bigint;
  debits bigint;
  credits bigint;
begin
  if tg_table_name = 'ledger_entries' then
    txn := new.txn_id;
  else
    txn := new.id;
  end if;
  select coalesce(sum(amount) filter (where side = 'debit'), 0),
         coalesce(sum(amount) filter (where side = 'credit'), 0)
    into debits, credits
    from engine.ledger_entries where txn_id = txn;
  if debits = 0 or debits <> credits then
    raise exception 'ledger transaction % is unbalanced: debits %, credits %', txn, debits, credits
      using errcode = 'check_violation';
  end if;
  return null;
end $$;

create constraint trigger ledger_entries_balanced after insert on engine.ledger_entries
  deferrable initially deferred for each row execute function engine.ledger_check_balanced();
create constraint trigger ledger_transactions_have_entries after insert on engine.ledger_transactions
  deferrable initially deferred for each row execute function engine.ledger_check_balanced();

create trigger ledger_entries_append_only before update or delete on engine.ledger_entries
  for each row execute function engine.forbid_mutation();
create trigger ledger_entries_no_truncate before truncate on engine.ledger_entries
  for each statement execute function engine.forbid_mutation();
create trigger ledger_transactions_append_only before update or delete on engine.ledger_transactions
  for each row execute function engine.forbid_mutation();
create trigger ledger_transactions_no_truncate before truncate on engine.ledger_transactions
  for each statement execute function engine.forbid_mutation();

-- Accounts start at zero, and only ledger_apply_entry changes a balance.
-- Every other column is fixed once the account exists.
create function engine.ledger_guard_account() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'ledger accounts cannot be deleted' using errcode = 'insufficient_privilege';
  end if;
  if tg_op = 'INSERT' then
    if new.balance <> 0 then
      raise exception 'ledger account % must start at zero: post an opening entry instead', new.code
        using errcode = 'insufficient_privilege';
    end if;
    return new;
  end if;
  if (new.id, new.code, new.unit, new.normal_side, new.allow_negative, new.created_at)
     is distinct from (old.id, old.code, old.unit, old.normal_side, old.allow_negative, old.created_at) then
    raise exception 'ledger account % can only change its balance', old.code
      using errcode = 'insufficient_privilege';
  end if;
  if new.balance <> old.balance
     and current_setting('engine.applying_ledger_entry', true) is distinct from 'on' then
    raise exception 'ledger account % balance changes only through ledger entries', old.code
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end $$;

create trigger ledger_accounts_guard before insert or update or delete on engine.ledger_accounts
  for each row execute function engine.ledger_guard_account();
create trigger ledger_accounts_no_truncate before truncate on engine.ledger_accounts
  for each statement execute function engine.forbid_mutation();

-- Append-only guards also fire when session_replication_role = replica, which
-- data restores set. The balance and zero-start triggers stay normal so a
-- restore can load balances as dumped.
alter table engine.events enable always trigger events_append_only;
alter table engine.events enable always trigger events_no_truncate;
alter table engine.ledger_entries enable always trigger ledger_entries_append_only;
alter table engine.ledger_entries enable always trigger ledger_entries_no_truncate;
alter table engine.ledger_transactions enable always trigger ledger_transactions_append_only;
alter table engine.ledger_transactions enable always trigger ledger_transactions_no_truncate;
alter table engine.ledger_accounts enable always trigger ledger_accounts_no_truncate;

-- Whole-ledger check: returns one row per problem, none when the books are
-- sound. Run nightly and at the end of the test suite.
create function engine.ledger_reconcile()
returns table (problem text, subject text, expected bigint, actual bigint)
language sql stable as $$
  select 'balance differs from entries', a.code, coalesce(e.net, 0), a.balance
    from engine.ledger_accounts a
    left join (
      select e.account_id,
             sum(case when e.side = a.normal_side then e.amount else -e.amount end) as net
        from engine.ledger_entries e
        join engine.ledger_accounts a on a.id = e.account_id
       group by e.account_id
    ) e on e.account_id = a.id
   where a.balance <> coalesce(e.net, 0)
  union all
  select 'debits differ from credits', t.unit,
         sum(e.amount) filter (where e.side = 'debit'),
         sum(e.amount) filter (where e.side = 'credit')
    from engine.ledger_entries e
    join engine.ledger_transactions t on t.id = e.txn_id
   group by t.unit
  having sum(e.amount) filter (where e.side = 'debit')
         is distinct from sum(e.amount) filter (where e.side = 'credit')
$$;

-- Row-level security on everything, with no policies: only the Engine's
-- server role (the table owner) reads or writes these tables.
alter table engine.users enable row level security;
alter table engine.zones enable row level security;
alter table engine.landmarks enable row level security;
alter table engine.households enable row level security;
alter table engine.riders enable row level security;
alter table engine.pickup_requests enable row level security;
alter table engine.events enable row level security;
alter table engine.jobs enable row level security;
alter table engine.ledger_accounts enable row level security;
alter table engine.ledger_transactions enable row level security;
alter table engine.ledger_entries enable row level security;
