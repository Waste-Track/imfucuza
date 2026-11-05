-- Paystack's checkout window pays a transaction our server set up. Its access
-- code is kept so reopening the window resumes that transaction instead of
-- starting a second one.
alter table engine.payments add column checkout_code text;
