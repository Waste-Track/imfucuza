-- Supervisor work: claiming and resolving review items, and rider payouts.

alter table engine.review_items
  add column assigned_to uuid references engine.users (id),
  add column claimed_at timestamptz,
  add column resolved_by uuid references engine.users (id);

-- A supervisor assignment has no offer distance: null makes the arrival
-- gate assume the longest trip.
alter table engine.dispatch_offers alter column distance_m drop not null;

-- Riders are paid to the number they registered with until a supervisor changes it.
update engine.riders r set payout_msisdn = u.phone_e164
  from engine.users u
 where u.id = r.user_id and r.payout_msisdn is null;

-- Riders are paid by hand over mobile money (the provider account has no
-- transfer API). A payout is requested, approved by a second supervisor when
-- large, sent, and only then recorded in the ledger with its MoMo reference.
create table engine.payouts (
  id uuid primary key default gen_random_uuid(),
  rider_id uuid not null references engine.riders (id),
  amount_pesewas integer not null check (amount_pesewas > 0),
  destination_msisdn text not null,
  status text not null
    check (status in ('pending_approval', 'approved', 'recorded', 'rejected')),
  requested_by uuid not null references engine.users (id),
  -- Large payouts need a second supervisor (threat model T15).
  approved_by uuid references engine.users (id),
  check (approved_by is null or approved_by <> requested_by),
  rejected_by uuid references engine.users (id),
  rejection_reason text,
  check (status <> 'rejected' or rejection_reason is not null),
  -- The mobile money transaction id, so one transfer is recorded once.
  momo_reference text unique,
  check ((status = 'recorded') = (momo_reference is not null)),
  recorded_by uuid references engine.users (id),
  ledger_txn_id bigint references engine.ledger_transactions (id),
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  recorded_at timestamptz
);

create index payouts_rider_idx on engine.payouts (rider_id, created_at);
create index payouts_open_idx on engine.payouts (status, created_at)
  where status in ('pending_approval', 'approved');

alter table engine.payouts enable row level security;
