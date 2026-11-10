# Feature specs

One folder per feature. Each has a `spec.md`. Add more files to the folder as needed, such as `tasks.md` for a feature in progress, or screen sketches.

| Feature | Status |
| --- | --- |
| [Accounts and sign-in](accounts/spec.md) | Shipped |
| [Paid pickup](paid-pickup/spec.md) | Shipped. The checkout window hasn't taken a live payment yet |
| [Dispatch](dispatch/spec.md) | Shipped |
| [Rider app](rider-app/spec.md) | Shipped |
| [Feature-phone USSD](feature-phone-ussd/spec.md) | Built, not live: no USSD gateway chosen |
| [Plastic collection](plastic-collection/spec.md) | Shipped |
| [Waste classifier](waste-classifier/spec.md) | v1 live. Thresholds not yet calibrated |
| [Rewards](rewards/spec.md) | Built. Needs partner shops |
| [Disputes and complaints](complaints/spec.md) | Shipped |
| [Supervisor console](supervisor-console/spec.md) | Shipped |
| [Rider payouts](rider-payouts/spec.md) | Shipped (sent by hand) |
| [Pilot monitoring](pilot-monitoring/spec.md) | Planned |

## Writing a spec

Copy this outline. Keep it short: rules with numbers belong in [policy.md](../policy.md), and the reasons behind a choice belong in [discussions.md](../discussions.md). How specs fit into the work is in [AGENTS.md](../../AGENTS.md).

```markdown
# Feature name

**Prefix:** ABC (unique, used in requirement IDs)
**Status:** Planned | In progress | Built, not live | Shipped

## Why
The user problem, in two or three sentences.

## How it works
What each person sees and does, step by step.

## Rules
Limits and edge cases. Link to policy.md for numbers.

## Requirements
- [ ] **ABC-1** One testable statement a reviewer can check. Cite a threat where it defends against one (threat model T9).

## Verify
The exact commands, and the test files, that prove the requirements.

## Code
Where it lives.

## Open work
What's left, or questions that block it.
```

Requirement IDs are permanent. Don't renumber them, and don't reuse an ID. Mark a dropped requirement ~~struck through~~ with the reason.

Prefixes in use: ACC, PAY, DSP, RDR, USSD, PLA, CLS, RWD, CMP, SUP, OUT, MON.

## Tasks

For work bigger than one change, add `tasks.md` beside the spec:

```markdown
# Tasks: feature name

- [ ] 1. Migration adding `engine.x` (PAY-10)
- [ ] 2. Domain function and its tests (PAY-10, PAY-11)
- [ ] 3. Route and API test (PAY-11)
- [ ] 4. Screen and its test (PAY-11)
- [ ] 5. Spec, policy and README updated
```

Tick tasks as they land. Leave the file in place when the feature ships, as a record.

Update a spec in the same pull request that changes the feature's behaviour.
