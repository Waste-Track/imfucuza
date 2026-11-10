# Waste classifier

**Prefix:** CLS
**Status:** v1 live on staging and production. Thresholds not calibrated.

## Why

No fixed rule separates clean plastic from contaminated or wrong-type waste, and fixed rules are easy to game for someone paid on the outcome. The classifier gives the Engine one opinion. The Engine's policy, riders and supervisors make the decision ([scope](../../README.md#3-where-learning-is-used)).

## How it works

- The Engine calls `POST /v1/verify` with a 5-minute signed photo link, the photo's hash and the declared plastic type ([contract](../../classifier-contract.md)).
- The classifier replies `accept` or `reject` with a calibrated confidence, or 422 if the photo is unusable.
- The Engine applies its thresholds ([policy](../../policy.md#photo-checks)). Anything in between goes to the rider.
- The service sleeps when idle on Render's free plan. The Engine's tick pings `/health` every 10 minutes to keep it awake.

Model details, test results and security controls are in the [intelligence README](../../../intelligence/README.md).

## Requirements

- [x] **CLS-1** The Engine calls the classifier for every plastic photo, and sends the pickup to a supervisor when it can't.
- [x] **CLS-2** Only the fields in the contract cross the boundary (threat model T11).
- [x] **CLS-3** The tick pings the classifier's `/health` every 10 minutes.
- [ ] **CLS-4** Thresholds are calibrated on labelled pilot photos, with false accepts and false rejects measured separately.
- [ ] **CLS-5** Only supervisor-audited labels feed retraining.
- [ ] **CLS-6** Production has its own classifier instance.

## Verify

```bash
cd backend && uv run pytest tests/test_classifier_client.py tests/test_verification_policy.py tests/test_internal_tick.py
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`intelligence/`, `backend/app/adapters/classifier.py`, `backend/app/domain/verification.py`.

## Open work

- Collect and label pilot photos from Madina, then set the thresholds from expected cost ([scope](../../README.md#4-intelligent-experience)).
- The service shares one Render instance between staging and production. Give production its own before the pilot.
