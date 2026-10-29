-- Riders with a smartphone can apply in the PWA. An application is a rider
-- whose user is 'pending': it gets no offers and can't use the rider routes
-- until a supervisor approves it.

alter table engine.users drop constraint users_status_check;
alter table engine.users add constraint users_status_check
  check (status in ('pending', 'active', 'suspended', 'deleted'));

-- What the rider gave when applying, for the supervisor to check in person.
alter table engine.riders
  add column tricycle_plate text check (char_length(tricycle_plate) between 2 and 20),
  add column work_area text check (char_length(work_area) between 2 and 100);
