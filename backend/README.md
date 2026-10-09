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

## Layout

| Path | Contents |
| --- | --- |
| `app/domain/ledger.py` | Double-entry ledger for money (pesewas) and points |
| `app/domain/pickups.py` | Pickup lifecycle: the transition table and `apply` |
| `app/domain/events.py` | Append-only event log, the source for monitoring signals |
| `app/jobs.py` | Background jobs, run by `POST /internal/tick` |
| `app/auth.py` | Supabase access-token verification |
| `supabase/migrations/` | Schema, ledger triggers, row-level security |

## Migrations

Create one with `supabase migration new <name>`. Engine tables go in the `engine` schema, which the Supabase Data API does not expose. Every table needs row-level security enabled, or CI fails. The ledger and event log are append-only: fix mistakes with a compensating posting, never an `UPDATE`.
