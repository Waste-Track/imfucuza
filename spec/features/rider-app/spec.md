# Rider app

**Prefix:** RDR
**Status:** Shipped

## Why

Smartphone riders need to see jobs, find the household, do the pickup and know what they've earned, without learning anything about "going online" ([discussions](../../discussions.md#2026-10-riders-are-online-while-the-app-is-open)).

## How it works

- **Applying.** A rider fills in their name, tricycle plate and work area, then sees "Waiting for approval" until a supervisor approves them. The approval comes by SMS.
- **Online.** Opening the app puts the rider on duty and shares their location from every rider screen. "Take a break" stops new offers until they tap back in or next open the app. GPS fixes taken offline are sent when the connection returns.
- **Jobs tab.** A map of the rider and their pickups, today's jobs and earnings, and any offer to accept or decline.
- **A job.** Arrive (within 150 m), then collect. For plastic, weigh and photograph, and decide if the check is unsure. Then enter the household's PIN.
- **History tab.** Finished jobs and what each earned. No addresses or phone numbers.
- **Earnings tab.** What's owed, today and this week, the payout number, and recent payouts.

## Requirements

- [x] **RDR-1** A rider can apply in the app and waits as pending, with no offers, until approved.
- [x] **RDR-2** An approved rider is on duty and shares their location as soon as the app opens. "Take a break" stops offers.
- [x] **RDR-3** A rider can take a job from offer to PIN in the app.
- [x] **RDR-4** History and earnings match the ledger, and history shows no addresses or phone numbers (threat model T12).
- [ ] **RDR-5** Offers reach a rider with the app closed (push notifications).

## Verify

```bash
cd backend && uv run pytest tests/test_rider_application.py tests/test_rider_history.py
```

```bash
cd frontend && npx vitest run src/screens/rider/RiderHome.test.tsx
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`frontend/src/screens/rider/`, `frontend/src/components/RiderMap.tsx`, `backend/app/api/riders.py`.

## Open work

- No push notifications. Offers appear only while the app is open, and they last 90 seconds.
