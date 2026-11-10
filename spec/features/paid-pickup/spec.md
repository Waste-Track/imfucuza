# Paid pickup

**Prefix:** PAY
**Status:** Shipped. The Paystack checkout window hasn't taken a live payment yet.

## Why

A household should pay a flat, known fee and pay only for a pickup that happened. A rider should be paid for every pickup the household confirms.

## How it works

1. The household asks for a pickup with their address and location (`POST /v1/pickups`).
2. They tap **Pay**. The Engine sets up a Paystack transaction (`POST /v1/pickups/{id}/checkout`) and Paystack's checkout window takes their network and mobile money number.
3. When the window reports success, the app asks the Engine to verify with Paystack (`POST /v1/pickups/{id}/payment/{reference}/check`). The webhook verifies it too, and doing it twice is harmless. The fee moves into the pickup's escrow account.
4. [Dispatch](../dispatch/spec.md) finds a rider. The household sees the rider's name and number once the rider accepts.
5. The rider arrives, collects and marks it collected. The household gets a PIN by SMS.
6. The household gives the rider the PIN, and the rider enters it. The pickup completes and escrow is released: 70% to the rider, the rest to the platform.

If no rider is found, or nobody confirms within 24 hours, the household is refunded. Before giving the PIN, a household can [dispute](../complaints/spec.md) instead.

Statuses: `awaiting_payment`, `pending_dispatch`, `offered`, `assigned`, `arrived`, `awaiting_pin`, then `completed`. Other endings: `cancelled`, `expired`, `failed`, `disputed`, `refunded` and `unconfirmed`. The full table is in `backend/app/domain/pickups.py`.

## Rules

Fee, windows, PIN limits and refunds: [policy](../../policy.md#paid-pickups). Reopening the checkout within 30 minutes resumes the same transaction.

## Requirements

- [x] **PAY-1** A household can request a pickup, pay in Paystack's checkout window, and confirm with a PIN. The rider's share lands in their ledger account.
- [x] **PAY-2** No payment within 15 minutes cancels the request. No rider within 45 minutes refunds it.
- [x] **PAY-3** No PIN within 24 hours refunds the household and opens a review item (threat model T2).
- [x] **PAY-4** A payment reported by both the app and the webhook settles once (threat model T14).
- [x] **PAY-5** Money changes only after the payment is confirmed with Paystack's own record (threat model T1).
- [x] **PAY-6** PINs are stored hashed, with limited tries and resends (threat model T4).
- [x] **PAY-7** Status changes go only through the transition table in `pickups.py`.
- [ ] **PAY-8** A live payment on production completes end to end.
- [ ] **PAY-9** Paystack's webhook deliveries succeed on production.

## Verify

```bash
cd backend && uv run pytest tests/test_refuse_pickup_flow.py tests/test_refuse_pickup_paths.py tests/test_refuse_pickup_money.py tests/test_refuse_pickup_guards.py tests/test_payment_checkout.py tests/test_paystack_adapter.py tests/test_pins_unit.py tests/test_pickup_state_machine.py tests/test_pickup_transitions_db.py tests/test_money.py tests/test_ledger_postings.py tests/test_ledger_db.py
```

```bash
cd frontend && npx vitest run src/screens/household/Payment.test.tsx src/components/PinPad.test.tsx src/lib/pickupStatus.test.ts
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`backend/app/domain/payments.py`, `money.py`, `pickups.py`, `pins.py`, `backend/app/adapters/paystack.py`, `backend/app/api/households.py`, `frontend/src/screens/household/Payment.tsx`, `NewRequest.tsx`, `PickupDetail.tsx`.

## Open work

- Check webhook signatures under the restricted key ([open question](../../discussions.md#open-questions)).
- Remove the direct-charge routes (`/payment`, `/payment/otp`) once nothing uses them.
