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
uv run pytest
supabase db reset && supabase db lint --fail-on error
```

## Migrations

Create one with `supabase migration new <name>`, which adds a file under `supabase/migrations/`. Every table in `public` needs row-level security enabled, or CI fails.
