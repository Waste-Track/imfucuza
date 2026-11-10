# Roadmap

What we are building, in order. Each item links to its feature spec. Open items name the requirement IDs they deliver. Tick an item off in the pull request that finishes it, and move items between stages as plans change.

Levels follow the [scope specification](README.md#3-where-learning-is-used). Level 1 is the core loop, Level 2 the extensions, and Level 3 the stretch goals.

## Done

Level 1, the core loop:
- [x] Phone sign-in, household registration and roles ([accounts](features/accounts/spec.md))
- [x] Paid pickup with escrow, PIN confirmation and refunds ([paid pickup](features/paid-pickup/spec.md))
- [x] Nearest-rider dispatch with offers, expiry and redispatch ([dispatch](features/dispatch/spec.md))
- [x] Plastic pickups weighed, photographed and checked, for points and rider pay ([plastic collection](features/plastic-collection/spec.md))
- [x] Waste classifier v1, wired in and kept awake ([waste classifier](features/waste-classifier/spec.md))
- [x] Supervisor console: review queue, riders, zones ([supervisor console](features/supervisor-console/spec.md))
- [x] Household and rider PWA, installable and readable offline
- [x] CI, staging deploys on merge, and production releases by hand

Level 2, extensions:
- [x] Disputes and complaints, with second approval past daily totals ([complaints](features/complaints/spec.md))
- [x] Vouchers and the zone leaderboard ([rewards](features/rewards/spec.md))
- [x] Rider payouts with approval and MoMo references ([rider payouts](features/rider-payouts/spec.md))
- [x] Riders apply in the app. The rider app has a map, history and earnings ([rider app](features/rider-app/spec.md))
- [x] USSD menus for riders, households and shops ([feature-phone USSD](features/feature-phone-ussd/spec.md))
- [x] Paystack checkout window for paying the pickup fee ([paid pickup](features/paid-pickup/spec.md))

## Now: ready for the pilot

Production has to run on its own before real households use it.

- [ ] Take one live payment on production, and check Paystack's webhook deliveries, PAY-8 and PAY-9 ([paid pickup](features/paid-pickup/spec.md#requirements))
- [ ] Give the production Engine a paid Render instance, then set up Supabase Cron for it, so dispatch, refunds and SMS run ([tech stack](tech-stack.md#rules-every-change-follows))
- [ ] Choose a USSD gateway and test it in its sandbox, USSD-6 ([feature-phone USSD](features/feature-phone-ussd/spec.md))
- [ ] Survey zones and landmarks in Madina and add them on production, USSD-7
- [ ] Set the data retention period and update the consent notice ([policy](policy.md#personal-data))
- [ ] Register and approve the pilot riders, and add the supervisors
- [ ] Sign up at least one partner shop for vouchers, RWD-6 ([rewards](features/rewards/spec.md))

## Next: during the pilot

- [ ] Monitoring page for signals S1 to S6, plus audit sampling, MON-1 to MON-3 ([pilot monitoring](features/pilot-monitoring/spec.md))
- [ ] Collect and label pilot photos, then calibrate the classifier thresholds, CLS-4 and CLS-5 ([waste classifier](features/waste-classifier/spec.md))
- [ ] A separate classifier instance for production, CLS-6
- [ ] Settle the open questions in [discussions](discussions.md#open-questions): roles per number, and Paystack webhook signatures

## Later

- [ ] Push notifications for job offers, so riders get them with the app closed, RDR-5
- [ ] Rider payouts through Paystack transfers instead of by hand
- [ ] Add supervisors from the console instead of SQL, ACC-6
- [ ] Remove the old direct-charge payment routes
- [ ] Spot collusion between households and riders from patterns across pickups, MON-4 ([known blind spots](README.md#13-known-blind-spots))
- [ ] Level 3: learned, demand-aware routing, trained on the pilot's dispatch log
