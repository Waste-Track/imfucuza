# Backend (Engine)

Python 3.12, FastAPI, Supabase. Design notes live in [`../spec`](../spec/README.md).

## Run locally

Needs [uv](https://docs.astral.sh/uv/), the [Supabase CLI](https://supabase.com/docs/guides/local-development) and Docker.

```bash
uv sync
uv run uvicorn app.main:app --reload    # http://127.0.0.1:8000/health
supabase start                          # local Postgres, Auth and Storage
```

## Checks CI runs

```bash
uv run ruff check .
uv run ruff format --check .
supabase db start && supabase db reset    # tests need the local database
supabase db lint --fail-on error
uv run pytest
```

## Configuration

Environment variables, read by `app/config.py`. With `ENVIRONMENT=local` (the default) every one has a working local value and the providers are in-memory fakes. Any other environment refuses to start until all of these are set:

| Variable | What |
| --- | --- |
| `DATABASE_URL` | Supabase session pooler connection string |
| `SUPABASE_URL` | Project URL, for verifying sign-in tokens |
| `INTERNAL_SECRET` | Shared with Supabase Cron, which calls `POST /internal/tick` |
| `PIN_PEPPER` | Long random secret mixed into stored PIN hashes. Never change it while PINs are live |
| `PAYMENT_PROVIDER`, `PAYSTACK_SECRET_KEY` | `paystack` and its secret key (test key on staging) |
| `SMS_GATEWAY`, `MNOTIFY_API_KEY` | `mnotify` and its API key. `SMS_SENDER_ID` defaults to `Imfucuza` |
| `SUPABASE_SMS_HOOK_SECRET` | The `v1,whsec_...` secret of the Auth "Send SMS" hook |

Supabase Auth settings the Engine relies on, locally in `supabase/config.toml` and on each hosted project:

- Phone sign-up on, with **phone confirmations on**. Accounts are linked by the token's phone claim, which is only safe once Supabase has confirmed the number.
- Send SMS hook pointing at `POST /hooks/supabase/send-sms`, so sign-in codes go out through mNotify.

## Layout

| Path | Contents |
| --- | --- |
| `app/domain/ledger.py` | Double-entry ledger for money (pesewas) and points |
| `app/domain/pickups.py` | Pickup lifecycle: the transition table and `apply` |
| `app/domain/events.py` | Append-only event log, the source for monitoring signals |
| `app/jobs.py` | Background jobs, run by `POST /internal/tick` |
| `app/domain/payments.py`, `money.py` | Collections, escrow and refunds |
| `app/domain/dispatch.py` | Nearest-rider offers, expiry and re-dispatch |
| `app/domain/pins.py` | Confirmation PINs |
| `app/adapters/` | Paystack and mNotify, plus in-memory fakes |
| `app/api/` | Routes for households, riders, supervisors and providers |
| `app/auth.py` | Supabase access-token verification |
| `supabase/migrations/` | Schema, ledger triggers, row-level security |

## Migrations

Create one with `supabase migration new <name>`. Engine tables go in the `engine` schema, which the Supabase Data API does not expose. Every table needs row-level security enabled, or CI fails. The ledger and event log are append-only: fix mistakes with a compensating posting, never an `UPDATE`.
