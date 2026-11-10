# Plastic collection

**Prefix:** PLA
**Status:** Shipped

## Why

Households that sort plastic should earn for it, and recyclers should get clean, sorted material. Payment depends on what was handed over, so each pickup is weighed and checked.

## How it works

1. The household asks for a plastic pickup and says what kind (PET, HDPE, PP or mixed). There is no fee.
2. [Dispatch](../dispatch/spec.md) finds a rider. The rider arrives, weighs the plastic and photographs it. A feature-phone rider's household can take the photo instead.
3. The Engine strips the photo's metadata and rejects reused photos, then asks the [classifier](../waste-classifier/spec.md).
4. The result:
   - **Accept:** the rider collects, and the household gets a PIN.
   - **Unsure:** the rider decides within 10 minutes. Their decision is logged.
   - **Reject:** nothing is collected, and the household is told why in plain words.
   - **Unreadable photo:** the rider retakes it.
   - **Classifier down, or the rider doesn't decide:** collect anyway, and a supervisor checks the photo before points or pay count.
5. The household gives its PIN. The points are confirmed, and the rider is paid per verified kg.

## Rules

Points, pay per kg, weight limits and daily limits: [policy](../../policy.md#plastic-for-points). Thresholds: [policy](../../policy.md#photo-checks).

## Requirements

- [x] **PLA-1** Accept, unsure (rider decides), reject, unreadable photo and classifier-down each lead to the outcome described above.
- [x] **PLA-2** Points stay pending until the household's PIN.
- [x] **PLA-3** Riders are paid per verified kg, and pay waits for a supervisor when a check is due.
- [x] **PLA-4** Reused photos are rejected (threat model T5).
- [x] **PLA-5** Heavy loads go to a supervisor, and extra requests are refused (threat model T6).
- [x] **PLA-6** A rider accepting plastic the check leaned toward rejecting goes to a supervisor (threat model T7).
- [x] **PLA-7** The rider's steps work over USSD.

## Verify

```bash
cd backend && uv run pytest tests/test_plastic_flow.py tests/test_plastic_ussd.py tests/test_photos.py tests/test_verification_policy.py
```

Name new tests after the requirement they check, or cite its ID in the test's docstring.

## Code

`backend/app/domain/plastic.py`, `photos.py`, `verification.py`, `frontend/src/screens/household/PlasticPhoto.tsx`, `frontend/src/screens/rider/RiderJob.tsx`.

## Open work

- Calibrate the thresholds on labelled pilot photos ([classifier](../waste-classifier/spec.md#open-work)).
- Agree what happens to collected plastic with a recycling partner, and how they report contamination.
