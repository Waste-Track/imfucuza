# Imfucuza

Smart Waste & Recycling Network for Madina, Accra. ICS 591 Team 2.

| Folder | Contents |
| --- | --- |
| [`spec/`](spec/README.md) | Scope and architecture specification |
| [`backend/`](backend/README.md) | Engine: API, dispatcher, ledger, PIN, USSD and SMS. Python, FastAPI, Supabase |
| [`frontend/`](frontend/README.md) | Household and rider PWAs |
| [`intelligence/`](intelligence/README.md) | Waste verification classifier |

## CI/CD

GitHub Actions runs [`pipeline.yml`](.github/workflows/pipeline.yml) on every pull request and push to `main`. It calls one workflow per folder, then a final `ci-ok` job.

### Checks

A folder's workflow runs only when that folder changes:

| Workflow | Runs |
| --- | --- |
| [`backend.yml`](.github/workflows/backend.yml) | `ruff check` and `ruff format --check`. Applies every migration to a fresh local Supabase database, lints the schema, fails if any table lacks row-level security or the `engine` schema is reachable through the Data API, then runs `pytest` against that database. |
| [`frontend.yml`](.github/workflows/frontend.yml) | Installs with the lockfile's package manager, then runs the `lint`, `typecheck`, `test` and `build` scripts that exist in `package.json`. |
| [`intelligence.yml`](.github/workflows/intelligence.yml) | Installs from `pyproject.toml` or `requirements.txt`, then runs `ruff check` and `pytest`. |

Every run also scans the full history for secrets with gitleaks, and lints workflow files with actionlint when they change.

`ci-ok` passes only when every job in the run passed or was skipped. Make it the single required check in the branch protection rule for `main`.

### Deploys

| Trigger | Target | What happens |
| --- | --- | --- |
| Push to `main` | `staging` | Deploys only the folders that changed, after `ci-ok` passes |
| Run **Release to production** (Actions tab) | `production` | Deploys a commit from `main` whose `ci-ok` passed. Needs approval if the `production` environment requires reviewers |

Order within a deploy:
1. Database migrations (`supabase db push`).
2. Backend, via a Render deploy hook pinned to the commit. The job then waits until `/health` reports that commit.
3. Frontend and intelligence, via their deploy hooks.

Migrations run before the new backend starts, so the old backend briefly runs against the new schema. Keep migrations backwards compatible: add columns and tables first, remove them in a later release.

### Configuration

Set these per environment under **Settings → Environments** (`staging` and `production`). A folder without its configuration is skipped with a warning, not failed.

| Name | Kind | Used for |
| --- | --- | --- |
| `SUPABASE_ACCESS_TOKEN` | secret | Supabase CLI login |
| `SUPABASE_DB_PASSWORD` | secret | Database password of that environment's project |
| `SUPABASE_PROJECT_REF` | variable | Project ref, e.g. `abcd1234efgh5678` |
| `RENDER_DEPLOY_HOOK_BACKEND` | secret | Render deploy hook URL of the backend service |
| `BACKEND_URL` | variable | Public URL of the backend, e.g. `https://engine-staging.onrender.com` |
| `FRONTEND_DEPLOY_HOOK` | secret | Deploy or build hook of the frontend host |
| `INTELLIGENCE_DEPLOY_HOOK` | secret | Deploy hook of the classifier service |

[`render.yaml`](render.yaml) defines the two backend services (`engine-staging`, `engine`). Auto-deploy is off, so Render only deploys commits the pipeline sends.
