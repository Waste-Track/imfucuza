# Disputes and complaints

**Prefix:** CMP
**Status:** Shipped

## Why

The system will sometimes be wrong: a rider who didn't collect, a photo wrongly rejected. Households need a way to say so, and a person must decide.

## How it works

- **Dispute, before the PIN.** The household reports a problem in the app or over USSD. Their PIN is cancelled and the money stays held. A supervisor either reissues the PIN or refunds.
- **Complaint, after the pickup.** The household complains within 7 days. A review item opens for a supervisor, who can refund or grant back points.
- **Second approval.** A refund or points grant past one supervisor's daily total waits for a second supervisor.

## Rules

[Policy](../../policy.md#disputes-complaints-and-supervisors). A dispute is never settled automatically. An undecided one is raised again after 48 hours.

## Requirements

- [x] **CMP-1** A dispute before the PIN freezes the money until a supervisor reissues the PIN or refunds.
- [x] **CMP-2** An undecided dispute is raised again after 48 hours, never settled automatically.
- [x] **CMP-3** A complaint within 7 days of the pickup opens a review item.
- [x] **CMP-4** Refunds and points granted back past one supervisor's daily total need a different supervisor (threat model T15).

## Verify

```bash
cd backend && uv run pytest tests/test_complaints.py
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`backend/app/domain/complaints.py`, `backend/app/domain/review.py`, `frontend/src/screens/supervisor/Approvals.tsx`.
