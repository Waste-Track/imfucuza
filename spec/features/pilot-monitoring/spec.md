# Pilot monitoring

**Prefix:** MON
**Status:** Planned

## Why

During the pilot we need to know whether pickups arrive within 2 hours, whether riders and households trust the outcomes, and whether the classifier is drifting. The [scope specification](../../README.md#12-measurement-and-monitoring-stub) defines six signals. The data for them is already collected, but nothing shows them yet.

## What to build

A monitoring page in the supervisor console, built from the event log and ledger:

| Signal | Shows | Source |
| --- | --- | --- |
| S1 | Classifier confidence over time, per plastic type | The classifier's `/metrics` endpoint, which already reports it |
| S2 | Override rate per rider | Rider decisions on unsure photos |
| S3 | False-accept and false-reject rates on audited samples | Supervisor verification decisions compared with the classifier's |
| S4 | Dispute rate and outcome, by rider and zone | Complaints and disputes |
| S5 | SMS delivery and USSD response rates | Delivery reports from mNotify and the USSD gateway |
| S6 | Time from request to completion, by zone and time of day | Pickup events |

Also:
- **Audit sampling.** A share of auto-accepted plastic goes to the review queue, so false accepts can be measured.
- **Engine health.** Last tick time, job queue backlog, and failed provider calls.

## Requirements

- [ ] **MON-1** Each of signals S1 to S6 has a number and a trend on the console.
- [ ] **MON-2** Audit sampling puts a set share of auto-accepted pickups in the review queue.
- [ ] **MON-3** An alert fires when ticks stop, or when the time to completion passes 2 hours for a day.
- [ ] **MON-4** Repeat household and rider pairings and unusually fast completions are flagged (threat model T8).

## Verify

No tests yet. Each requirement needs one before it's ticked.

## Open work

- Decide the audit sampling rate.
- Decide where alerts go (SMS to a supervisor, or email).
