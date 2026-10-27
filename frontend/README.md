# Frontend

One installable PWA for households and smartphone riders, built with React and Vite. Whoever signs in decides which screens open: a household gets the household app and an approved rider the rider app. A new phone chooses between setting up a household and applying as a rider. A rider's application waits for a supervisor to approve it (`PUT /v1/admin/riders/{id}/status`). Riders a supervisor registered directly (`POST /v1/admin/riders`), including feature-phone riders, are linked on their first sign-in.

Screens follow the design canvas "Smart Waste PWA, Rider and USSD Screens". The API contract is the backend's OpenAPI schema at `/openapi.json`.

New visitors land on a page that explains the service and offers "Get my waste collected", "Register as a rider" or "Sign in". Registering is two steps: confirm the phone number with an SMS code (Supabase phone sign-in; the number is the account), then the household or rider form.

## Supervisor console

Supervisors and admins who sign in get the console instead: a desktop-first page (it also works on a phone) with the review queue (claim an item, see the pickup, photos, photo check and timeline, then act and resolve), rider applications and rider management (approve, suspend, payouts, payout number, registering feature-phone riders), payouts waiting for approval or to record, complaint decisions waiting for a second supervisor, zones and landmarks, and rewards (vouchers, partner shops, monthly shop report).

A supervisor account is created in the database, once per person, and linked on their first sign-in with that number:

```sql
insert into engine.users (role, phone_e164, name, consent_version, consent_at, consent_channel)
values ('supervisor', '+233241234567', 'Ama Mensah', '2026-10-v1', now(), 'paper');
```

## Run locally

Needs Node 24 and a local Engine (see [`../backend/README.md`](../backend/README.md)).

```bash
cp .env.example .env.local   # fill in the Supabase URL and publishable key
npm install
npm run dev                  # http://localhost:5173, /v1 is proxied to the Engine on :8000
```

On hosted projects, sign-in codes go out by SMS through the Supabase "Send SMS" hook and mNotify. Local Supabase sends no SMS, so for local sign-in add test numbers under `[auth.sms.test_otp]` in `backend/supabase/config.toml` (for example `233241234567 = "123456"`), restart Supabase, and don't commit that change.

To try the camera and GPS on a real phone, it has to be served over HTTPS. `npm run build && npm run preview -- --host` and a tunnel such as `cloudflared` is the quickest way.

## Configuration

Build-time variables (Vite inlines them, so none of them may be secret):

| Variable | What |
| --- | --- |
| `VITE_SUPABASE_URL` | The Supabase project the Engine verifies tokens against |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | That project's publishable key (`sb_publishable_...`) |
| `VITE_API_URL` | The Engine's public URL, e.g. `https://engine-staging.onrender.com`. Empty in development |

When the PWA is served from its own origin, add that origin to the Engine's `CORS_ALLOWED_ORIGINS`.

## Hosting

Two Vercel projects on team Geeks, one per environment, each pointing at its own Engine:

| Environment | Vercel project | URL | Engine |
| --- | --- | --- | --- |
| staging | `imfucuza-staging` | https://imfucuza-staging.vercel.app | https://engine-staging-yk1g.onrender.com |
| production | `imfucuza` | https://imfucuza.vercel.app | https://engine-qqgd.onrender.com |

[`vercel.json`](vercel.json) turns off Vercel's own Git deploys, so only the pipeline deploys: it builds the commit that passed `ci-ok` and deploys it with the Vercel CLI to the project's production URL (see the root README, CI/CD). Vercel previews aren't used, because they sit behind a Vercel login and change URL on every deploy, which CORS can't allow. The `VITE_*` variables live in each project's Production environment, and `vercel pull` brings them into the build.

To deploy by hand from this folder: `npx vercel link`, then `npx vercel deploy --prod`.

## Offline

The app shell is cached, so it opens without a connection. The last pickups, points and jobs that loaded stay readable through a network-first cache. Actions such as requesting a pickup or entering a PIN need a connection and say so when they fail. A rider's GPS fixes taken offline are kept and sent when the connection returns.

## Layout

| Path | What |
| --- | --- |
| `src/App.tsx` | Picks household, rider or sign-up screens from `GET /v1/me` |
| `src/lib/api.ts`, `types.ts` | Engine calls and the shapes they return |
| `src/lib/pickupStatus.ts` | What each pickup status means to a household |
| `src/screens/household/` | Home, new request, payment, status and PIN, plastic photo, points and vouchers |
| `src/screens/rider/` | Duty and offers, then the job: arrive, collect or weigh and photograph, decide, enter PIN |
| `src/components/` | Shared UI, the PIN pad and photo capture |

## What CI runs

`npm run lint`, `npm run typecheck`, `npm test` and `npm run build`.
