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
| `PHOTO_STORE`, `SUPABASE_SECRET_KEY` | `supabase` and the project's secret key (`sb_secret_...`), for the private `pickup-photos` bucket |
| `CLASSIFIER`, `CLASSIFIER_URL`, `CLASSIFIER_TOKEN` | `http` with the classifier's base URL and token (contract in [`../spec/classifier-contract.md`](../spec/classifier-contract.md)), or `none` to send every plastic pickup to a supervisor |
| `USSD_PROVIDER`, `USSD_WEBHOOK_TOKEN`, `USSD_CODE` | Optional. The one USSD provider accepted (`africastalking`, `arkesel` or `mnotify`), the 32+ character secret it calls at `POST /webhooks/ussd/{provider}/{token}`, and the code shown in SMS. USSD is off until all three are set |
| `USSD_ALLOWED_IPS`, `TRUST_FORWARDED_FOR` | Optional. Addresses the USSD provider calls from, and whether to read the caller from `X-Forwarded-For` (true behind Render) |
| `POINTS_PER_KG`, `RIDER_PAY_PESEWAS_PER_KG`, `RIDER_REVIEW_S` | Optional. Plastic points per kg by type (JSON, PET, HDPE and PP 10, mixed 5), rider pay per verified kg (GHS 1), and how long a rider has to decide when the check is unsure (10 min) |
| `PLASTIC_MAX_WEIGHT_G`, `PLASTIC_REVIEW_ABOVE_G`, `PLASTIC_HOUSEHOLD_REVIEW_ABOVE_G`, `PLASTIC_PICKUPS_PER_DAY` | Optional. Limits on weight-paid pickups: the heaviest allowed (25 kg), above which a supervisor checks the weight before anyone is paid (10 kg, or 5 kg when the household took the photo for a feature-phone rider), and requests per household a day (2, one open at a time) |
| `COMPLAINT_WINDOW_DAYS`, `DISPUTE_ESCALATE_S`, `COMPLAINT_REFUND_DAILY_PESEWAS`, `COMPLAINT_POINTS_DAILY` | Optional. How long after a pickup a household can complain (7 days), when an undecided dispute is raised again (48 h), and what one supervisor can refund (GHS 50) or grant back (500 points) a day before a second must approve |
| `VOUCHER_VALID_DAYS`, `LEADERBOARD_CONSENT_VERSION` | Optional. How long a voucher can be used (30 days, then its points come back), and the leaderboard notice version households accept before they're named |
| `VERIFY_ACCEPT_AT`, `VERIFY_REJECT_AT`, `VERIFICATION_POLICY_VERSION` | Optional. Classifier confidence needed to accept (0.90) or reject (0.80). Change the version with the thresholds |
| `PAYOUT_HOLD_S`, `PAYOUT_SECOND_APPROVAL_PESEWAS` | Optional. How long rider earnings wait for disputes (48 h), and the daily amount per rider above which a second supervisor approves a payout (GHS 200) |

Supabase Auth settings the Engine relies on, locally in `supabase/config.toml` and on each hosted project:

- Phone sign-up on, with **phone confirmations on**. Accounts are linked by the token's phone claim, which is only safe once Supabase has confirmed the number.
- Send SMS hook pointing at `POST /hooks/supabase/send-sms`, so sign-in codes go out through mNotify.

Supabase Cron calls `POST /internal/tick` every 10 seconds, which runs every timer: offer expiry, refunds, PIN deadlines and queued SMS. Set it up once per hosted project:

1. Check the Render plan. A ticked service never sleeps, and two always-on free services use more than Render's 750 free hours a month, so give production a paid instance before it gets its secrets.
2. On the dashboard's Vault page, add `engine_tick_url` (`https://<engine-host>/internal/tick`) and `engine_internal_secret` (the `INTERNAL_SECRET` value). Avoid the SQL editor for this: it keeps what you type as a saved snippet.
3. Check the schedule with `select jobname, username, active from cron.job`: two jobs, both run as `postgres`.

Until both secrets exist the schedule runs but sends nothing, as it does locally. Cron's own history shows success either way, since it only queues the request. To see whether ticks land, watch `last_tick_at` on `/health`, or run `select status_code, error_msg, count(*), max(created) from net._http_response group by 1, 2` for the last 6 hours.

To rotate the secret, deploy the new `INTERNAL_SECRET` to Render, then update `engine_internal_secret` on the Vault page straight away. Ticks in between get a 401 and the next one 10 seconds later succeeds.

To have Cron tick a local server, set `engine_tick_url` to `http://host.docker.internal:8000/internal/tick`, since pg_net runs inside the database container. Tests refuse to start until that secret is deleted again.

USSD notes:

- The mNotify USSD format comes from its spec alone and is untested: confirm session ids and the new-session signal in a sandbox before choosing it.
- Feature-phone riders pick their location from zones and landmarks. `supabase/seed.sql` loads sample ones locally only. Hosted databases need surveyed ones added through `POST /v1/admin/zones` before USSD riders can be dispatched.

Rider payouts are sent by hand over mobile money. A supervisor requests the payout, a second one approves it if it's over the threshold, someone sends the money to the payout number on the request, then records the MoMo reference. Only that last step touches the ledger.

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
| `app/domain/ussd.py` | USSD menus for feature-phone riders and households |
| `app/domain/plastic.py` | Plastic collection: the check at the door, rider and supervisor decisions, points and rider pay |
| `app/domain/complaints.py` | Disputes before the PIN, complaints after it, and supervisors' decisions with a second approver past a daily total |
| `app/domain/rewards.py` | Vouchers bought with points and redeemed by shops over USSD, and the monthly zone leaderboard |
| `app/domain/photos.py`, `verification.py` | Photo intake (EXIF stripped, fingerprinted) and the verification decision policy |
| `app/domain/supervision.py` | Supervisor console: review queue, interventions, riders, payouts, zones |
| `app/adapters/` | Paystack, mNotify, Supabase Storage and the classifier client, plus in-memory fakes |
| `app/api/` | Routes for households, riders, supervisors and providers |
| `app/auth.py` | Supabase access-token verification |
| `supabase/migrations/` | Schema, ledger triggers, row-level security |

## Migrations

Create one with `supabase migration new <name>`. Engine tables go in the `engine` schema, which the Supabase Data API does not expose. Every table needs row-level security enabled, or CI fails. Never add `engine`, `net` or `vault` to the Data API's exposed schemas. The ledger and event log are append-only: fix mistakes with a compensating posting, never an `UPDATE`.
