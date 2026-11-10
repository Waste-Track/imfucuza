# Working on Imfucuza

Imfucuza is built spec-first. The [`spec/`](spec/README.md) folder says what to build and why. Code follows the spec, and a change in behaviour starts with a change in the spec.

## Read first

| File | Read it for |
| --- | --- |
| [spec/mission.md](spec/mission.md) | Who it's for, and the principles that settle trade-offs |
| [spec/roadmap.md](spec/roadmap.md) | What to work on, in order |
| [spec/features/](spec/features/README.md) | The spec for the feature you're touching: requirements with IDs, how to verify them, where the code is |
| [spec/policy.md](spec/policy.md) | Fees, limits and data rules, each with its setting |
| [spec/tech-stack.md](spec/tech-stack.md) | The stack, and the rules every change follows |
| [spec/threat-model.md](spec/threat-model.md) | How the system can be cheated, and what stops it |
| [spec/discussions.md](spec/discussions.md) | Decisions already made, and open questions. Don't reopen a decision without saying why |

## How to do a piece of work

1. **Find the spec.** Find the roadmap item and its feature spec. If none fits, write a new one in `spec/features/<name>/spec.md` from the outline in [spec/features/README.md](spec/features/README.md).
2. **Change the spec first.** Add or edit requirements, giving each new one the next free ID (`PAY-10`). Set the status to `In progress`. If the work changes a fee, limit or data rule, update [policy.md](spec/policy.md). If it settles a choice, add an entry to [discussions.md](spec/discussions.md).
3. **Get the spec approved.** Show the person you're working with the spec change and wait for their approval before writing code, unless they already approved it.
4. **Plan the tasks.** For anything bigger than a single change, write `tasks.md` in the feature folder: a checklist of small steps, each naming the requirement IDs it serves.
5. **Build with tests.** Write each test against a requirement, naming the ID in its docstring or test name. Follow the rules in [tech-stack.md](spec/tech-stack.md#rules-every-change-follows). Where code defends against a threat, cite it as a comment such as `(threat model T9)`. A new kind of attack gets a new entry in the threat model.
6. **Verify.** Run the commands in the spec's Verify section, then the full checks below.
7. **Close the loop in the same pull request.** Tick the requirements and tasks you finished. Update the feature's status, and the roadmap item if it's done. Update the README that documents any new setting or route.

If the code and a spec disagree, don't quietly pick one. Say which is wrong, then fix the spec or the code.

## Checks

Backend, from `backend/`. The tests need the local Supabase database, which needs Docker:

```bash
supabase start            # once
supabase migration up --local   # after adding a migration
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Frontend, from `frontend/`:

```bash
npm run lint && npm run typecheck && npm test && npm run build
```

Classifier, from `intelligence/`: `pytest && ruff check .`

CI runs the same checks. `ci-ok` must pass before merging.

## Never

- Edit a migration that's already on `main`. Add a new one instead.
- Change money or points other than through ledger postings, or a pickup's status other than through the transition table.
- Put a secret in the web app, in Git, or in a chat message. `VITE_*` values are public.
- Commit local-only config, such as test OTP numbers in `backend/supabase/config.toml`.
- Run anything against production unless the person asks. Production is released by hand with the **Release to production** workflow.

## Git

Work on a branch and open a pull request into `main`. Commit messages are a short plain sentence saying what changed for users or operators, for example "Pay in Paystack's checkout window".
