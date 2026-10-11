-- Plastic collection: what the household declared, what the rider weighed, and
-- what the pickup earns once verified and confirmed.

alter table engine.pickup_requests
  add constraint pickup_requests_declared_type
    check (declared_type in ('pet', 'hdpe', 'pp', 'mixed_plastic')),
  add constraint pickup_requests_plastic_declared
    check (offering <> 'plastic' or declared_type is not null),
  -- Worked out when the weight is submitted, at that time's rates, so the
  -- household is told the same figure it later earns.
  add column points_due integer check (points_due >= 0),
  add column rider_pay_pesewas integer check (rider_pay_pesewas >= 0);

create table engine.pickup_photos (
  id uuid primary key,
  pickup_id uuid not null references engine.pickup_requests (id),
  storage_key text not null unique,
  -- Of the stored JPEG, and of the upload as received.
  sha256 text not null,
  upload_sha256 text not null,
  dhash bigint not null,
  source text not null check (source in ('rider', 'household')),
  captured_by uuid not null references engine.users (id),
  created_at timestamptz not null default now()
);

create index pickup_photos_pickup_idx on engine.pickup_photos (pickup_id, created_at);
create index pickup_photos_sha256_idx on engine.pickup_photos (sha256);
create index pickup_photos_upload_sha256_idx on engine.pickup_photos (upload_sha256);

-- Every step of a verification, one row each, never changed (engine-design.md
-- section 5). Only supervisor-checked rows may ever feed retraining.
create table engine.verification_decisions (
  id bigint generated always as identity primary key,
  pickup_id uuid not null references engine.pickup_requests (id),
  photo_id uuid references engine.pickup_photos (id),
  stage text not null check (stage in ('classifier', 'policy', 'rider', 'supervisor')),
  decision text not null,
  confidence double precision,
  reason text,
  model_version text,
  policy_version text,
  latency_ms integer,
  actor_id uuid references engine.users (id),
  -- The rider went against the classifier's leaning.
  is_override boolean,
  created_at timestamptz not null default now()
);

create index verification_decisions_pickup_idx on engine.verification_decisions (pickup_id, id);

create trigger verification_decisions_append_only
  before update or delete on engine.verification_decisions
  for each row execute function engine.forbid_mutation();
create trigger verification_decisions_no_truncate
  before truncate on engine.verification_decisions
  for each statement execute function engine.forbid_mutation();
alter table engine.verification_decisions enable always trigger verification_decisions_append_only;
alter table engine.verification_decisions enable always trigger verification_decisions_no_truncate;

alter table engine.pickup_photos enable row level security;
alter table engine.verification_decisions enable row level security;
