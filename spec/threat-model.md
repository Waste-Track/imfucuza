# Threat model

How someone could cheat Imfucuza or harm its users, and what stops them. Code comments cite threats by ID, for example `(threat model T9)`.

The original threat model was never committed. Code cited only T3, T9, T13 and T15, so those keep their numbers. The other IDs were assigned when this file was rebuilt from the code in November 2026.

When a change adds a way to move money, points or personal data, check it against this list. Add a threat if none fits, with the next free ID, and cite the ID in the code that defends against it.

| ID | Threat | Who | Controls | Where |
| --- | --- | --- | --- | --- |
| T1 | A forged payment webhook marks an unpaid pickup as paid | Anyone | Webhooks must carry a valid Paystack signature. Even then, every change in money is confirmed against Paystack's own record of the transaction, never taken from the webhook body | `adapters/paystack.py`, `domain/payments.py` |
| T2 | A rider marks a pickup done without collecting | Rider | The fee is released only when the household's PIN is entered. With no PIN within 24 hours, the household is refunded and a supervisor looks into it | `domain/pins.py` |
| T3 | A hijacked phone or account drains value | Outsider with a stolen phone or SIM | Sign-in needs an SMS code to the number. A household holds at most 3 unused vouchers. A rider's new payout number holds payouts for 48 hours | `domain/rewards.py`, `domain/supervision.py` |
| T4 | Someone guesses or reuses a PIN | Rider, outsider | PINs are stored only as an HMAC with a secret pepper, allow 5 wrong tries and last 24 hours. Resends are capped at 4 per pickup, or 8 with a supervisor | `domain/pins.py` |
| T5 | A reused photo earns points twice | Household, rider | Photos are fingerprinted on upload and after processing, and repeats are rejected. EXIF metadata is stripped | `domain/photos.py` |
| T6 | Plastic weight is inflated | Rider, household | Pickups are capped at 25 kg. A supervisor checks weights above 10 kg, or above 5 kg when the household took the photo. A rider can enter the weight at most 3 times. Households get 2 plastic pickups a day, one at a time | `domain/plastic.py`, [policy](policy.md#plastic-for-points) |
| T7 | A rider accepts plastic the check leaned toward rejecting, since riders are paid for what they accept | Rider | That kind of override waits for a supervisor before anyone is paid. Every rider decision is logged with the photo | `domain/plastic.py` |
| T8 | A household and rider fake a completion together | Household and rider | No control on a single pickup. Only patterns across pickups can show it ([known blind spots](README.md#13-known-blind-spots)) | Planned: [pilot monitoring](features/pilot-monitoring/spec.md) |
| T9 | A rider fakes their location to win offers or arrive early | Rider | A rider can self-report their location at most 6 times an hour, and self-reported locations count as less precise. Arriving from the app needs GPS within 150 m. A self-reported arrival can't come faster than a tricycle could travel | `domain/dispatch.py`, `domain/pickup_flow.py` |
| T10 | A forged call reaches the Engine's provider or cron routes | Anyone | USSD calls need the secret path token and, optionally, an allowed IP address. The SMS hook needs a Standard Webhooks signature with a time limit. `/internal/tick` needs `INTERNAL_SECRET` | `api/ussd.py`, `api/webhooks.py`, `api/internal.py` |
| T11 | Personal data leaks to the classifier, or a crafted photo link makes it fetch from elsewhere | Outsider, a compromised classifier | Only a 5-minute signed photo link and the plastic type cross the boundary. The classifier accepts links only to our storage host, and refuses unknown fields | [classifier-contract.md](classifier-contract.md), `intelligence/service.py` |
| T12 | Personal data is read straight from the database or seen by the wrong person | Outsider, other users | Engine tables are in a schema the Data API doesn't expose, with row-level security on every table. Phone numbers are shown only while a job is underway. Rider history shows no addresses or numbers | `supabase/migrations/`, `api/riders.py` |
| T13 | A shop guesses voucher codes | Partner shop | Codes are 10 digits and stored masked. A shop can type at most 10 wrong codes an hour and 30 a day | `domain/rewards.py` |
| T14 | A retry charges, refunds or pays out twice | System fault | Payments, refunds and checkouts are keyed by reference, and a checkout resumes for 30 minutes. Each collection is refunded once. A payout reaches the ledger once, when its MoMo reference is recorded | `domain/payments.py`, `domain/supervision.py` |
| T15 | One supervisor gives away money or points | Supervisor | Refunds and points granted back past one supervisor's daily total need a second supervisor, as do payouts above GH₵ 200 a day per rider. The database won't let the requester approve their own payout, and payouts close together count toward the same daily total | `domain/complaints.py`, `domain/supervision.py`, `supabase/migrations/20261012090000_supervisor.sql` |

Paths are under `backend/app/` unless shown otherwise.

## Not covered yet

- **T8, collusion**, as above.
- **Edited photos.** A small change to a genuine photo could flip the classifier's decision.
- **Slow drift in a rider's override rate**, which stays under any fixed alert level.
- **Leaderboard impersonation** is only partly covered: names like "Imfucuza" or "admin" are refused, but other misleading names aren't.
