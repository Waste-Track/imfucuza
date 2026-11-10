# Rewards

**Prefix:** RWD
**Status:** Built. Needs partner shops before households can spend points.

## Why

Points matter only if they can be spent. A zone leaderboard gives neighbours a reason to keep sorting.

## How it works

- **Vouchers.** A household picks a voucher from the catalog and pays for it in points. The code arrives by SMS. A partner shop redeems it over USSD. Supervisors add shops and voucher products, and get a report of what each shop redeemed so they can settle with it.
- **Leaderboard.** A monthly ranking of households in each zone by points. A household appears by name only after accepting the leaderboard notice.

## Rules

- A voucher is valid for 30 days. If it isn't used, the points come back.
- Withdrawing a product stops new vouchers, but vouchers already issued still work.
- Codes are stored masked. A shop that types codes matching nothing uses up an allowance.
- Supervisors can remove a household's name from the leaderboard.

## Requirements

- [x] **RWD-1** A household can buy a voucher with points, and a shop can redeem it.
- [x] **RWD-2** Unused vouchers expire after 30 days and return their points.
- [x] **RWD-3** A household holds at most 3 unused vouchers (threat model T3).
- [x] **RWD-4** Wrong codes from a shop are limited (threat model T13).
- [x] **RWD-5** The leaderboard names only households that agreed, and refuses names that pass as staff.
- [ ] **RWD-6** At least one partner shop is signed up and added on production.

## Verify

```bash
cd backend && uv run pytest tests/test_rewards.py
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`backend/app/domain/rewards.py`, `frontend/src/screens/household/Points.tsx`, `frontend/src/screens/supervisor/Rewards.tsx`.

## Open work

Shops redeem only over USSD, so redemption waits on the [USSD gateway](../feature-phone-ussd/spec.md).
