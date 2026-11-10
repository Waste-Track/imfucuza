# Tech stack

What Imfucuza is built with, where it runs, and the rules every change follows. How to run each part is in its folder's README.

## Parts

| Part | Folder | Built with |
| --- | --- | --- |
| Engine (backend) | [`backend/`](../backend/README.md) | Python 3.12, FastAPI, psycopg 3 (async pool), Pydantic settings. Managed with uv, checked with ruff and pytest |
| Database, sign-in and photo storage | `backend/supabase/` | Supabase: Postgres with migrations, phone sign-in (Auth), Storage (private `pickup-photos` bucket), Cron and pg_net for the job tick, Vault for the tick's secrets |
| Web app (PWA) | [`frontend/`](../frontend/README.md) | React, Vite, TypeScript, React Router, vite-plugin-pwa (Workbox), Leaflet with OpenStreetMap tiles, `@supabase/supabase-js`, `@paystack/inline-js`. Checked with ESLint, tsc and Vitest |
| Waste classifier | [`intelligence/`](../intelligence/README.md) | FastAPI service serving a ViT-S/16 model as ONNX, with weights pulled from a private Hugging Face repo pinned by commit and SHA-256. Contract: [classifier-contract.md](classifier-contract.md) |

## Outside services

| Service | Used for |
| --- | --- |
| Paystack | Mobile money collections through its checkout window, refunds, and payment webhooks. Live key on production, test key on staging |
| mNotify | SMS: sign-in codes (through the Supabase Send SMS hook), PINs, job offers and notices |
| A USSD gateway | Feature-phone menus. Africa's Talking, Arkesel and mNotify are supported. The provider isn't chosen yet ([discussions](discussions.md)) |
| OpenStreetMap | Map tiles in the rider app |

## Environments

| | Staging | Production |
| --- | --- | --- |
| Web app (Vercel) | https://imfucuza-staging.vercel.app | https://imfucuza.vercel.app |
| Engine (Render) | https://engine-staging-yk1g.onrender.com | https://engine-qqgd.onrender.com |
| Classifier (Render) | https://classifier-staging.onrender.com, shared by both | same |
| Paystack mode | Test | Live |
| Deployed by | Every push to `main` that passes CI | The **Release to production** workflow, run by hand |

CI and deploys are described in the [root README](../README.md#cicd).

## Rules every change follows

**Money and points**
- Amounts are whole pesewas (`int`), never floats. GH₵ 1 is 100 pesewas.
- Money and points move only through double-entry postings in the ledger (`app/domain/ledger.py`). The ledger and the event log are append-only. Fix mistakes with a compensating posting, never an `UPDATE`.
- A pickup changes status only through the transition table in `app/domain/pickups.py`.
- Calls to a provider that can be retried use an idempotency key or a stored reference, so a retry never charges or pays twice.

**Database**
- Engine tables live in the `engine` schema, which the Supabase Data API doesn't expose. Every table has row-level security on, or CI fails.
- Migrations must work with the backend version before them, because they run first. Add columns and tables in one release and remove them in a later one.

**Secrets**
- Secret keys live only in Render, GitHub environments and Supabase Vault. Nothing secret goes into the web app: `VITE_*` values are public.
- No secrets, model weights or datasets in Git. CI scans the history with gitleaks.

**Slow work**
- Anything slow or that can fail (SMS, refunds, dispatch rounds, timers) runs as a job. Supabase Cron calls `POST /internal/tick` every 10 seconds to run due jobs.

**Testing**
- Backend tests run against a real local Supabase database, not mocks of it. Providers have in-memory fakes in `app/adapters/fakes.py`.
- Each change comes with tests for the behaviour it adds. CI's single required check is `ci-ok`.

**Writing**
- Text users see is short and plain, and says what to do next.
