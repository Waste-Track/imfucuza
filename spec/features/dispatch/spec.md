# Dispatch

**Prefix:** DSP
**Status:** Shipped

## Why

A pickup is only useful if a rider takes it quickly. The pilot has 5 to 10 riders and no trip history, so dispatch is a rule: offer the job to the nearest available rider ([discussions](../../discussions.md#m1-rule-based-dispatch)).

## How it works

1. Once a pickup is paid (or a plastic pickup is requested), it waits for dispatch.
2. Each round offers it to the nearest on-duty rider within 5 km who has no active job and a recent location.
3. App riders get the offer in the app and have 90 seconds to accept. USSD riders get an SMS and have 5 minutes.
4. A decline or timeout moves the offer to the next rider. Rounds repeat every 3 minutes until a rider accepts or 45 minutes pass.
5. A supervisor can assign a rider or send the pickup back to dispatch at any time.

## Rules

- A location counts as recent for 10 minutes if it came from GPS, or 60 minutes if the rider picked a zone or landmark over USSD. Self-reported locations are treated as less precise.
- A rider who misses 3 offers in a row is treated as unreachable for 30 minutes.
- A rider can change a self-reported location at most 6 times an hour.
- A rider must be within 150 m of the household to mark themselves arrived from the app.
- Every round is logged in full, so a learned router can later be trained and judged against it.

## Requirements

- [x] **DSP-1** A pickup waiting for dispatch is offered to the nearest eligible rider: on duty, within 5 km, with no active job and a recent location.
- [x] **DSP-2** A decline or timeout moves the offer to the next rider, and rounds repeat until 45 minutes pass.
- [x] **DSP-3** A pickup with no rider after 45 minutes expires and is refunded.
- [x] **DSP-4** Supervisors can assign a rider and redispatch.
- [x] **DSP-5** Self-reported locations are rate-limited and count as less precise (threat model T9).
- [x] **DSP-6** Every dispatch round is logged in full.

## Verify

```bash
cd backend && uv run pytest tests/test_refuse_pickup_guards.py tests/test_refuse_pickup_paths.py tests/test_supervisor.py tests/test_jobs.py
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`backend/app/domain/dispatch.py`, `backend/app/jobs.py`, `backend/app/api/riders.py` (offers, locations, duty).

## Open work

- Needs production Cron. Without the tick, no rounds run ([roadmap](../../roadmap.md)).
- Learned routing is a stretch goal for after the pilot.
