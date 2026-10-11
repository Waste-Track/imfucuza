-- Rewards: points spent on vouchers that partner shops redeem by USSD, and a
-- monthly leaderboard per zone that households opt in to.

alter table engine.users drop constraint users_role_check;
alter table engine.users add constraint users_role_check
  check (role in ('household', 'rider', 'supervisor', 'admin', 'shop'));
alter table engine.events drop constraint events_actor_type_check;
alter table engine.events add constraint events_actor_type_check
  check (actor_type in ('household', 'rider', 'supervisor', 'admin', 'shop', 'system', 'provider'));

create table engine.partner_shops (
  id uuid primary key default gen_random_uuid(),
  -- The phone the shop dials from to redeem vouchers.
  user_id uuid not null unique references engine.users (id),
  -- Short enough to keep the household's SMS to one message.
  name text not null check (char_length(name) between 2 and 40),
  created_by uuid not null references engine.users (id),
  created_at timestamptz not null default now()
);

create table engine.voucher_products (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(name) between 2 and 40),
  points_cost integer not null check (points_cost > 0),
  -- What the operator owes the shop for one redeemed voucher.
  face_value_pesewas integer not null check (face_value_pesewas >= 0),
  active boolean not null default true,
  created_by uuid not null references engine.users (id),
  created_at timestamptz not null default now()
);

create table engine.vouchers (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references engine.households (id),
  product_id uuid not null references engine.voucher_products (id),
  -- Digits only, so a shop can type it on any phone.
  code text not null unique check (code ~ '^[0-9]{10}$'),
  points integer not null check (points > 0),
  status text not null default 'active' check (status in ('active', 'used', 'expired')),
  issued_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz,
  used_at_shop_id uuid references engine.partner_shops (id),
  check ((status = 'used') = (used_at is not null and used_at_shop_id is not null))
);

create index vouchers_household_idx on engine.vouchers (household_id, issued_at);
create index vouchers_shop_idx on engine.vouchers (used_at_shop_id, used_at);

-- A separate consent (Act 843 s20, threat model section 3), stored with its
-- notice version, time and channel. No consent, no name on the board.
alter table engine.households
  add column leaderboard_name text check (char_length(leaderboard_name) between 2 and 20),
  add column leaderboard_consent_version text,
  add column leaderboard_consent_at timestamptz,
  add column leaderboard_consent_channel text check (leaderboard_consent_channel in ('pwa', 'ussd')),
  add constraint households_leaderboard_consent check (
    (leaderboard_name is null) = (leaderboard_consent_at is null)
    and (leaderboard_consent_at is null) = (leaderboard_consent_version is null)
    and (leaderboard_consent_at is null) = (leaderboard_consent_channel is null)
  );

-- One household per name, so nobody can pass as someone else on a board.
create unique index households_leaderboard_name_key
  on engine.households (lower(leaderboard_name)) where leaderboard_name is not null;

-- A household's zone for the leaderboard: the one it was given, or else the
-- nearest to its home, if that is within a few zone radii. Distances are a flat
-- approximation, fine at city scale.
create function engine.household_zone(h engine.households) returns smallint
language sql stable
set search_path = ''
as $$
  select coalesce(h.zone_id, (
    select z.id from engine.zones z
     where h.lat is not null and h.lng is not null
       and ((z.lat - h.lat) * 111000) ^ 2
           + ((z.lng - h.lng) * 111000 * cos(radians(h.lat))) ^ 2 <= (5 * z.radius_m) ^ 2
     order by (z.lat - h.lat) ^ 2 + (z.lng - h.lng) ^ 2
     limit 1))
$$;

revoke execute on function engine.household_zone(engine.households) from public;

alter table engine.partner_shops enable row level security;
alter table engine.voucher_products enable row level security;
alter table engine.vouchers enable row level security;
