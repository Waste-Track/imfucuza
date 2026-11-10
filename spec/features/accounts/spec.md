# Accounts and sign-in

**Prefix:** ACC
**Status:** Shipped

## Why

Most users have a phone number but not always an email address, and USSD identifies callers by number. So the phone number is the account.

## How it works

1. A visitor lands on the landing page and picks "Get my waste collected", "Register as a rider" or "Sign in".
2. They confirm their number with an SMS code (Supabase phone sign-in, with codes sent through mNotify by the Send SMS hook).
3. A new number fills in the household form or the rider application, and accepts the consent notice.
4. The app asks the Engine who the number is (`GET /v1/me`) and opens the household app, the rider app, a "waiting for approval" screen, or the supervisor console.
5. If the number already has an account in a different role from the one chosen, the app says so. The person can continue in that role or use a different number.

Supervisors are added in the database, once per person, and linked on their first sign-in. Riders a supervisor registered are linked the same way.

## Rules

- One number is one account with one role ([discussions](../../discussions.md#2026-10-one-phone-number-one-account)).
- Ghanaian numbers only.
- Accounts are linked by the token's phone claim, which is safe only because Supabase confirms the number first.
- Consent version and channel are stored with each account ([policy](../../policy.md#personal-data)).

## Requirements

- [x] **ACC-1** A new number can register as a household or apply as a rider after confirming an SMS code.
- [x] **ACC-2** Signing in opens the screens for that number's role (`GET /v1/me`).
- [x] **ACC-3** A number registered in another role is told so, not switched silently.
- [x] **ACC-4** Sign-in codes go out through mNotify on hosted projects, and the SMS hook rejects unsigned calls (threat model T10).
- [x] **ACC-5** The consent version and channel are stored with every account.
- [ ] **ACC-6** Admins can add supervisors from the console.

## Verify

```bash
cd backend && uv run pytest tests/test_auth.py tests/test_me.py tests/test_people.py tests/test_supabase_sms_hook.py tests/test_supabase_config.py
```

```bash
cd frontend && npx vitest run src/App.test.tsx src/screens/Landing.test.tsx
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`backend/app/api/me.py`, `backend/app/domain/people.py`, `backend/app/auth.py`, `backend/app/api/webhooks.py` (SMS hook), `frontend/src/session.tsx`, `frontend/src/App.tsx`, `frontend/src/screens/Landing.tsx`, `SignIn.tsx`, `Welcome.tsx`.

## Open work

- Decide whether one number may hold more than one role ([open question](../../discussions.md#open-questions)).
- Supervisors are still added with SQL. A console screen for admins would remove that step.
