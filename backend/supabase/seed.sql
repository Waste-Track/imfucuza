-- Local development only: `supabase db reset` loads this, `supabase db push` never does.
-- Coordinates are rough estimates for testing, not surveyed points. Replace them
-- with points checked on the ground before any pilot uses zones or landmarks.

insert into engine.zones (name, lat, lng, radius_m, ussd_index) values
  ('Madina Market', 5.6830, -0.1660, 600, 1),
  ('Redco', 5.6905, -0.1690, 700, 2),
  ('Estate', 5.6760, -0.1600, 700, 3),
  ('Adenta', 5.7000, -0.1580, 900, 4);

insert into engine.landmarks (zone_id, name, lat, lng, ussd_index)
select z.id, l.name, l.lat, l.lng, l.ussd_index
  from (values
    ('Madina Market', 'Market square', 5.6828, -0.1662, 1),
    ('Madina Market', 'Zongo Junction', 5.6850, -0.1645, 2),
    ('Madina Market', 'Madina station', 5.6815, -0.1675, 3),
    ('Redco', 'Redco flats', 5.6910, -0.1695, 1),
    ('Redco', 'Social Welfare', 5.6890, -0.1675, 2),
    ('Estate', 'Estate junction', 5.6765, -0.1605, 1),
    ('Estate', 'Firestone', 5.6745, -0.1585, 2),
    ('Adenta', 'Adenta barrier', 5.7010, -0.1570, 1),
    ('Adenta', 'SDA junction', 5.6990, -0.1600, 2)
  ) as l (zone, name, lat, lng, ussd_index)
  join engine.zones z on z.name = l.zone;
