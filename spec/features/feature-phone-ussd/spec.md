# Feature-phone USSD

**Prefix:** USSD
**Status:** Built, not live. No USSD gateway has been chosen.

## Why

Many riders don't have smartphones, and some households don't either. Anything a rider must do has to work by dialling a code.

## How it works

The Engine answers `POST /webhooks/ussd/{provider}/{token}`. Each keypress is one request, and a session row remembers the screen.

- **Riders:** start or stop a shift, set their location by choosing a zone and landmark, answer job offers (the offer SMS says which option to choose), enter the plastic weight and material, and enter the household's PIN.
- **Households:** enter the PIN from their SMS, or report a problem with a recent pickup.
- **Partner shops:** redeem a household's voucher code ([rewards](../rewards/spec.md)).

## Rules

- Screens stay within 160 characters.
- The handler only reads and writes the database. SMS goes out through the job queue.
- Only the configured provider, calling from its allowed addresses with the secret token, is accepted.
- Zones and landmarks must be surveyed and added through the console before USSD riders can be dispatched.

## Requirements

- [x] **USSD-1** Riders can start a shift, set their location, answer offers, enter weight and material, and enter the PIN by USSD.
- [x] **USSD-2** Households can enter a PIN or report a problem by USSD.
- [x] **USSD-3** Partner shops can redeem vouchers by USSD.
- [x] **USSD-4** Every screen fits in 160 characters.
- [x] **USSD-5** Only the configured provider with the secret token is accepted (threat model T10).
- [ ] **USSD-6** A gateway is chosen and tested in its sandbox.
- [ ] **USSD-7** Surveyed zones and landmarks for Madina are loaded on production.
- [ ] **USSD-8** A feature-phone rider completes a real pickup.

## Verify

```bash
cd backend && uv run pytest tests/test_ussd_flow.py tests/test_ussd_codecs.py tests/test_plastic_ussd.py
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`backend/app/domain/ussd.py`, `backend/app/api/ussd.py`, `backend/app/adapters/` (USSD request formats).

## Open work

Choose the gateway ([open question](../../discussions.md#open-questions)), then set `USSD_PROVIDER`, `USSD_WEBHOOK_TOKEN` and `USSD_CODE`.
