# Mission

Make waste collection in Madina, Accra, something a household can count on, and make sorted plastic worth something to the household that sorts it.

## The problem

Formal waste companies run fixed routes that miss whole streets. Informal aboboyaa (tricycle) riders fill part of the gap, but nothing confirms a rider is coming, agrees a fair price, or proves a pickup happened. Households that sort plastic have no dependable way to be rewarded for it, and recyclers receive mixed, contaminated material.

## Who it is for

| Who | What they need from Imfucuza |
| --- | --- |
| Households | A pickup that turns up within hours, at a flat fee they pay only for a pickup that happened. Points for sorted plastic that they can spend. |
| Tricycle riders | Steady jobs near where they are, pay they can see and trust, and no need for a smartphone. |
| Supervisors | One place to settle what the system can't: disputes, uncertain photos, rider approvals and payouts. |
| Recyclers | Cleaner, sorted plastic. |

## What we offer

1. **Paid waste pickup.** A household asks for a pickup and pays a flat fee. The money is held, and the rider is paid only once the household confirms with a PIN.
2. **Plastic for points.** A household hands over sorted plastic. It is weighed and checked, and the household earns points it can spend on vouchers at partner shops. A zone leaderboard keeps neighbours competing.

Both share one set of riders, one backend and one app.

## Pilot goals (end of Q1)

| Goal | Target |
| --- | --- |
| Active users | 100 |
| Sorted plastic collected | 10 tonnes |
| Request to completed pickup | Within 2 hours |
| Disputed pickups | Few, and every one settled by a supervisor |

## Principles

These decide trade-offs when a feature spec doesn't.

- **Money moves only on proof.** A household's fee is released to the rider only when the household gives the PIN. If nobody confirms, the household gets it back.
- **The classifier advises, people decide.** The waste classifier never pays anyone. When it is unsure or unavailable, a rider or supervisor decides.
- **Feature phones are first-class.** Anything a rider must do can be done over USSD and SMS.
- **Plain words.** Users see outcomes ("we couldn't confirm this was plastic"), never scores or codes.
- **Collect only what we use.** Personal data stays with the Engine. The classifier never sees names, numbers, locations or payments. See [policy.md](policy.md).
- **Every decision leaves a trail.** Pickups, ledger postings and supervisor actions are logged and never edited, so disputes and audits can be answered.

## Not doing

Tracking hardware, financing tricycles, and learned routing before there is trip data. The reasons are in the [scope specification](README.md#7-out-of-scope).
