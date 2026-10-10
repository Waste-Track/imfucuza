-- Tables for the paid refuse pickup: payments, dispatch, PINs, SMS and the
-- supervisor queue (engine-design.md sections 1 to 4, 6 and 8).

-- Timestamps for monitoring signal S6 (request to completion).
alter table engine.pickup_requests
  add column paid_at timestamptz,
  add column first_offered_at timestamptz,
  add column assigned_at timestamptz,
  add column arrived_at timestamptz,
  add column collected_at timestamptz;

-- Provider clearing may now go below zero (a refund returns the full amount,
-- the provider keeps its fee). Correct an account created before that rule.
alter table engine.ledger_accounts disable trigger ledger_accounts_guard;
update engine.ledger_accounts set allow_negative = true where code = 'provider_clearing';
alter table engine.ledger_accounts enable trigger ledger_accounts_guard;

-- Payments ------------------------------------------------------------------

create table engine.payments (
  id uuid primary key default gen_random_uuid(),
  pickup_id uuid not null references engine.pickup_requests (id),
  direction text not null check (direction in ('collection', 'refund')),
  provider text not null,
  -- Our reference for a collection. For a refund, the collection it refunds.
  reference text not null,
  provider_refund_id text,
  network text check (network in ('mtn', 'telecel', 'airteltigo')),
  amount_pesewas integer not null check (amount_pesewas > 0),
  fee_pesewas integer check (fee_pesewas >= 0),
  status text not null default 'pending' check (status in ('pending', 'succeeded', 'failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (direction, reference)
);

create index payments_pickup_idx on engine.payments (pickup_id);

-- Every verified webhook, once. A redelivery hits the unique key and is dropped.
create table engine.inbound_webhooks (
  id bigint generated always as identity primary key,
  provider text not null,
  event_id text not null,
  kind text not null,
  reference text not null,
  received_at timestamptz not null default now(),
  unique (provider, event_id)
);

create table engine.idempotency_keys (
  user_id uuid not null references engine.users (id),
  key text not null check (length(key) between 8 and 200),
  request_hash text not null,
  response_status integer not null,
  response_body jsonb not null,
  created_at timestamptz not null default now(),
  primary key (user_id, key)
);

-- Dispatch ------------------------------------------------------------------

create table engine.rider_locations (
  id bigint generated always as identity primary key,
  rider_id uuid not null references engine.riders (id),
  source text not null check (source in ('gps', 'landmark', 'zone')),
  lat double precision not null check (lat between -90 and 90),
  lng double precision not null check (lng between -180 and 180),
  accuracy_m integer check (accuracy_m >= 0),
  mock_location boolean not null default false,
  pickup_id uuid references engine.pickup_requests (id),
  reported_at timestamptz not null,
  received_at timestamptz not null default now()
);

create index rider_locations_latest_idx on engine.rider_locations (rider_id, received_at desc);

create table engine.dispatch_offers (
  id uuid primary key default gen_random_uuid(),
  pickup_id uuid not null references engine.pickup_requests (id),
  rider_id uuid not null references engine.riders (id),
  rank smallint not null,
  distance_m integer not null,
  location_source text not null,
  channel text not null check (channel in ('pwa', 'ussd')),
  sent_at timestamptz not null default now(),
  expires_at timestamptz not null,
  responded_at timestamptz,
  response text check (response in ('accepted', 'declined', 'expired', 'undeliverable', 'withdrawn')),
  check ((response is null) = (responded_at is null))
);

-- One open offer per pickup, and one per rider.
create unique index dispatch_offers_open_per_pickup on engine.dispatch_offers (pickup_id)
  where response is null;
create unique index dispatch_offers_open_per_rider on engine.dispatch_offers (rider_id)
  where response is null;
create index dispatch_offers_pickup_idx on engine.dispatch_offers (pickup_id, sent_at);

-- PINs ----------------------------------------------------------------------

create table engine.pins (
  id uuid primary key default gen_random_uuid(),
  pickup_id uuid not null references engine.pickup_requests (id),
  -- HMAC-SHA256 of the PIN with a server-side pepper. The PIN itself is never stored.
  pin_hmac text not null,
  status text not null default 'active'
    check (status in ('active', 'confirmed', 'locked', 'expired', 'superseded')),
  attempts smallint not null default 0,
  expires_at timestamptz not null,
  confirmed_at timestamptz,
  confirmed_via text check (confirmed_via in ('household_pwa', 'household_ussd', 'rider_entry')),
  created_at timestamptz not null default now()
);

create unique index pins_one_live_per_pickup on engine.pins (pickup_id)
  where status in ('active', 'locked');

-- Notifications ---------------------------------------------------------------

create table engine.notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references engine.users (id),
  pickup_id uuid references engine.pickup_requests (id),
  channel text not null check (channel in ('sms')),
  template text not null,
  -- PINs are masked: the stored body is safe to show a supervisor.
  body_masked text not null,
  provider text,
  provider_message_id text,
  status text not null default 'sent' check (status in ('sent', 'delivered', 'failed', 'unknown')),
  created_at timestamptz not null default now(),
  status_checked_at timestamptz
);

create index notifications_pickup_idx on engine.notifications (pickup_id);

-- Supervisor queue ----------------------------------------------------------

create table engine.review_items (
  id uuid primary key default gen_random_uuid(),
  type text not null check (type in (
    'household_complaint', 'low_confidence_pattern', 'rider_override_rate',
    'manual_verification', 'recycler_contamination', 'audit_sample', 'pin_issue',
    'dispatch_stalled', 'payment_mismatch'
  )),
  status text not null default 'open' check (status in ('open', 'resolved')),
  pickup_id uuid references engine.pickup_requests (id),
  rider_id uuid references engine.riders (id),
  household_id uuid references engine.households (id),
  payload jsonb not null default '{}',
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolution text
);

-- One open item per problem: the same type on one pickup can have several reasons.
create unique index review_items_one_open_per_problem
  on engine.review_items (type, pickup_id, (coalesce(payload->>'reason', '')))
  where status = 'open';
create index review_items_open_idx on engine.review_items (status, created_at);

alter table engine.payments enable row level security;
alter table engine.inbound_webhooks enable row level security;
alter table engine.idempotency_keys enable row level security;
alter table engine.rider_locations enable row level security;
alter table engine.dispatch_offers enable row level security;
alter table engine.pins enable row level security;
alter table engine.notifications enable row level security;
alter table engine.review_items enable row level security;
