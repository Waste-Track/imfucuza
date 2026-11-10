# Discussions

Decisions and the reasons for them, newest first, plus questions still open. Add an entry when a choice changes the product, a policy or the stack, so nobody has to ask again why things are the way they are. Feature specs link here rather than repeating the reasoning.

## Open questions

| Question | Why it matters | Needed by |
| --- | --- | --- |
| Which USSD gateway? | Feature-phone riders can't get jobs until one is chosen and tested. mNotify's USSD format is untested and needs a sandbox check | Pilot |
| How long do we keep personal data? | Act 843 and the consent notice need a retention period for numbers, GPS fixes and photos | Pilot |
| Does Paystack sign webhooks with the restricted key or the main secret key? | If it uses the main key, the Engine rejects webhooks. Payments still settle through the app's check, but a payment where the app closes partway wouldn't settle until the household reopens it | First live payments |
| Should one phone number hold more than one role? | Today one number is one account with one role, so a rider can't also be a household on the same number | Before households ask for it |
| Production Render plan | Two always-on free services go over Render's free hours. The production Engine needs a paid instance before Cron ticks it | Before production Cron |

## Decisions

### 2026-11: Spec-driven development

Work starts from `spec/`: mission, roadmap, policy, tech stack, threat model and one spec per feature, each requirement with a permanent ID. AI agents and people follow the same loop, set out in `AGENTS.md`. The design notes (`engine-design.md`, `engine-plan.md`) and threat model that code comments cited were never committed. The comments now point at the feature specs, and the threat model was rebuilt from the code in `threat-model.md`.

### 2026-11: Pay in Paystack's checkout window

The Engine used to charge mobile money directly through Paystack's charge API, with the app collecting the network and an OTP. Now the Engine sets up a transaction and the app opens Paystack's checkout window with its access code.

- Paystack's window handles the network, number, approval prompt and OTP, so the Engine no longer has to handle each network's approval steps.
- Households can pay from a number other than the one they signed in with.
- No Paystack key is in the app. Reopening the window within 30 minutes resumes the same transaction, so a household can't pay twice.
- The app asks the Engine to verify with Paystack as soon as the window reports success, so the pickup moves on without waiting for the webhook.

The direct-charge routes are still in the Engine.

### 2026-10: Riders are online while the app is open

Riders were asked to "go online", which confused them. Now opening the app puts a rider on duty and shares their location. "Take a break" stops new offers until they return. The rider home screen shows a map, today's jobs and earnings, history and earnings tabs.

### 2026-10: Show readable errors when the Engine crashes

A crash reply had no CORS headers, so the browser reported "no internet" for what was really a server error. Crash replies now carry CORS headers and a plain message, and provider errors are logged with the reason Paystack gave.

### 2026-10: Turn the classifier on and keep it awake

The classifier sleeps when idle and takes about 24 seconds to wake, longer than the Engine's 8-second timeout. The Engine now calls it for every plastic photo, and the cron tick pings its `/health` every 10 minutes.

### 2026-10: One phone number, one account

Accounts are identified by a phone number confirmed by an SMS code. That fits users who may not have email, and USSD identifies callers by number anyway. A number that already has an account is told so at sign-up, and isn't quietly switched to the role it chose. For testing several roles, use Supabase test numbers for the supervisor and rider.

### 2026-10: Riders can apply in the app

Riders were registered only by supervisors. Smartphone riders can now apply in the app and wait as `pending` until a supervisor approves them. Supervisors still register feature-phone riders.

### 2026-10: Paystack for payments

The [scope specification](README.md#10-operational-dependencies) left the payment provider open. Paystack was chosen because it supports Ghanaian mobile money, refunds and webhooks. Mobile money can't hold a payment the way a card does, so the Engine takes the payment at request time, keeps it in a per-pickup escrow account in its own ledger, and releases or refunds it from there.

### M1: Rule-based dispatch

Offer each pickup to the nearest available rider. With 5 to 10 riders and no trip history there is nothing to learn from. Every dispatch round is logged in full so a learned router can be trained and judged against it later.

### M1: Separate false-accept and false-reject rates

Blended accuracy can rise while false accepts rise too, which means paying for fraud. The two rates are tracked separately.
