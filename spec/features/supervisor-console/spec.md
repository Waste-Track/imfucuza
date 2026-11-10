# Supervisor console

**Prefix:** SUP
**Status:** Shipped

## Why

Some decisions shouldn't be automatic: unsure photos, disputes, rider approvals, payouts. Supervisors need one place to make them and see the evidence.

## How it works

A desktop-first page (it also works on a phone) that opens when a supervisor or admin signs in.

- **Review queue.** Claim an item, see the pickup, photos, photo check and timeline, act (reissue a PIN, refund, assign, redispatch, cancel, decide a verification), then resolve it.
- **Riders.** Approve applications, suspend riders, register feature-phone riders, set payout numbers.
- **Payouts.** Request, approve and record rider payouts ([rider payouts](../rider-payouts/spec.md)).
- **Approvals.** Complaint decisions waiting for a second supervisor.
- **Zones.** Zones and landmarks for USSD locations.
- **Rewards.** Vouchers, partner shops and the monthly shop report.

## Rules

- Each supervisor action is logged with who did it.
- One supervisor can't approve their own request where a second approval is needed.

## Requirements

- [x] **SUP-1** Every review item type can be claimed, acted on and resolved from the console.
- [x] **SUP-2** Every supervisor action in the Engine's API is reachable from the console.
- [x] **SUP-3** Each supervisor action is logged with who did it.
- [ ] **SUP-4** A monitoring view (see pilot monitoring).

## Verify

```bash
cd backend && uv run pytest tests/test_supervisor.py
```

```bash
cd frontend && npx vitest run src/screens/supervisor/admin.test.ts
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`frontend/src/screens/supervisor/`, `backend/app/api/supervisor.py`, `backend/app/domain/supervision.py`, `review.py`.

## Open work

- Supervisors are added with SQL ([accounts](../accounts/spec.md#open-work)).
- The console has no monitoring view yet ([pilot monitoring](../pilot-monitoring/spec.md)).
