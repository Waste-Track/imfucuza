# Policy

The rules the product enforces: fees, who gets paid when, limits on supervisors, and what happens to personal data. Most numbers here are settings. The setting's name is in brackets, and its default is in `backend/app/config.py`. To change a rule, change this file and the setting in the same pull request, and say why in [discussions.md](discussions.md).

## Paid pickups

| Rule | Value |
| --- | --- |
| Pickup fee | GH₵ 10 (`REFUSE_FEE_PESEWAS`) |
| Rider's share of the fee | 70%. The rest is platform revenue (`RIDER_SHARE_PERCENT`) |
| Time to pay after requesting | 15 minutes, then the request is cancelled (`PAYMENT_WINDOW_S`) |
| Time to find a rider | 45 minutes, then the request expires and the fee is refunded (`DISPATCH_WINDOW_S`) |
| When the rider is paid | When the household gives its PIN. Until then the fee sits in that pickup's escrow |
| PIN | Sent by SMS once the rider has collected. It lasts 24 hours and allows 5 wrong tries (`PIN_TTL_S`, `PIN_MAX_ATTEMPTS`) |
| Nobody confirms within 24 hours | The household is refunded, and a supervisor looks into it |

## Plastic for points

| Rule | Value |
| --- | --- |
| Points per kg | PET, HDPE and PP: 10. Mixed plastic: 5 (`POINTS_PER_KG`) |
| Rider pay per verified kg | GH₵ 1, paid by the platform (`RIDER_PAY_PESEWAS_PER_KG`) |
| Heaviest pickup | 25 kg (`PLASTIC_MAX_WEIGHT_G`) |
| A supervisor checks the weight first | Above 10 kg, or above 5 kg when the household took the photo for a feature-phone rider (`PLASTIC_REVIEW_ABOVE_G`, `PLASTIC_HOUSEHOLD_REVIEW_ABOVE_G`) |
| Requests per household | 2 a day, one open at a time (`PLASTIC_PICKUPS_PER_DAY`) |
| When points count | Pending until the household gives its PIN |
| Vouchers | Valid 30 days. Unused points come back (`VOUCHER_VALID_DAYS`) |
| Leaderboard | A household's name appears only after it accepts the leaderboard notice. Supervisors can remove a name |

## Photo checks

| Rule | Value |
| --- | --- |
| Classifier accepts | Confidence 0.90 or above (`VERIFY_ACCEPT_AT`) |
| Classifier rejects | Confidence 0.80 or above (`VERIFY_REJECT_AT`) |
| In between | The rider decides within 10 minutes (`RIDER_REVIEW_S`) |
| Rider doesn't decide, or the classifier is down | Collect anyway. A supervisor checks the photo before the points and the rider's pay count |
| Policy version | Change `VERIFICATION_POLICY_VERSION` whenever a threshold changes, so every decision records the rules it was made under |

The thresholds are placeholders until there is labelled pilot data. How they will be calibrated is in the [scope specification](README.md#4-intelligent-experience).

## Disputes, complaints and supervisors

| Rule | Value |
| --- | --- |
| Before giving the PIN | The household can dispute. The money stays held until a supervisor reissues the PIN or refunds. An undecided dispute is raised again after 48 hours, and never settled automatically (`DISPUTE_ESCALATE_S`) |
| After the pickup | The household can complain for 7 days. A complaint opens a review item (`COMPLAINT_WINDOW_DAYS`) |
| One supervisor's daily limit | Refunds of GH₵ 50 and 500 points granted back. Past that, a second supervisor approves (`COMPLAINT_REFUND_DAILY_PESEWAS`, `COMPLAINT_POINTS_DAILY`) |

## Rider payouts

| Rule | Value |
| --- | --- |
| Hold before earnings can be paid out | 48 hours, to leave room for disputes (`PAYOUT_HOLD_S`) |
| Second approval | Needed above GH₵ 200 per rider per day (`PAYOUT_SECOND_APPROVAL_PESEWAS`) |
| How it's paid | By hand, over mobile money, to the payout number on the request. The ledger changes only when the MoMo reference is recorded |
| Who can join | Supervisors register riders, or approve a rider who applied in the app. A pending rider gets no offers |

## Personal data

Handled under the Data Protection Act, 2012 (Act 843).

- **What we hold:** phone numbers, names, household addresses and locations, rider GPS fixes, pickup photos and the decisions made on them.
- **Consent:** households and riders accept the consent notice before registering. The version and channel they accepted are stored, and the current version is `CONSENT_VERSION`.
- **Who sees what:** while a rider is on the way or at the door, the household sees the rider's first name and number, and the rider sees the household's. Job history shows a rider no addresses or phone numbers.
- **The classifier** receives a 5-minute signed photo link and the declared plastic type. It gets no name, number, location or payment details.
- **Photos** have their EXIF data, including GPS, removed before they are stored.
- **Identity:** one phone number is one account with one role. Sign-in is by SMS code to that number.
- **Retention:** not decided yet. It must be set before the pilot starts ([discussions](discussions.md#open-questions)).

## Engineering

See the rules in [tech-stack.md](tech-stack.md#rules-every-change-follows).
