# Rider payouts

**Prefix:** OUT
**Status:** Shipped. Money is sent by hand.

## Why

Riders earn into their ledger account with every confirmed pickup. They need that money on their mobile money wallet, without anyone paying a rider twice or paying for a pickup that's later refunded.

## How it works

1. A supervisor requests a payout for a rider. Only earnings older than 48 hours count.
2. A second supervisor approves it if the rider's payouts that day pass GH₵ 200.
3. Someone sends the money over mobile money to the payout number on the request.
4. They record the MoMo reference. Only this step changes the ledger.

The rider sees what's owed and past payouts on the Earnings tab.

## Rules

[Policy](../../policy.md#rider-payouts).

## Requirements

- [x] **OUT-1** A payout can be requested, approved when needed, and recorded.
- [x] **OUT-2** Only earnings older than 48 hours can be paid out.
- [x] **OUT-3** The ledger changes only when the MoMo reference is recorded, and only once (threat model T14).
- [x] **OUT-4** The requester can't approve their own payout, and payouts close together count toward the same daily total (threat model T15).
- [x] **OUT-5** A new payout number holds payouts for 48 hours (threat model T3).

## Verify

```bash
cd backend && uv run pytest tests/test_supervisor.py tests/test_rider_history.py
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`backend/app/domain/supervision.py`, `backend/app/domain/money.py`, `frontend/src/screens/supervisor/Payouts.tsx`, `frontend/src/screens/rider/RiderEarnings.tsx`.

## Open work

Paying out through Paystack transfers would remove the manual step, but would need the transfer permission on the key and a funded balance.
