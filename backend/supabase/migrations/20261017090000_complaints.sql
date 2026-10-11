-- Complaint outcomes that cost the platform: a refund after the pickup was
-- completed, and points granted back after a wrongful rejection. Past a
-- supervisor's daily total a second supervisor approves (threat model T15).

create table engine.complaint_decisions (
  id uuid primary key default gen_random_uuid(),
  pickup_id uuid not null references engine.pickup_requests (id),
  outcome text not null check (outcome in ('late_refund', 'points_grant')),
  -- Pesewas for a late refund, points for a grant.
  amount integer not null check (amount > 0),
  note text not null check (char_length(note) between 3 and 500),
  status text not null check (status in ('pending_approval', 'applied', 'rejected')),
  decided_by uuid not null references engine.users (id),
  approved_by uuid references engine.users (id),
  check (approved_by is null or approved_by <> decided_by),
  rejected_by uuid references engine.users (id),
  created_at timestamptz not null default now(),
  settled_at timestamptz
);

-- One live decision of each kind per pickup, so it can't be paid twice.
create unique index complaint_decisions_one_per_pickup
  on engine.complaint_decisions (pickup_id, outcome) where status <> 'rejected';
create index complaint_decisions_by_supervisor
  on engine.complaint_decisions (decided_by, created_at);

alter table engine.complaint_decisions enable row level security;
