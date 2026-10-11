"""Plastic verification (engine-design.md section 5). The classifier gives a
verdict and a confidence. This policy, owned by the Engine, decides what
happens next. Escalation to a supervisor is pattern-based and lives elsewhere."""

from dataclasses import dataclass
from enum import StrEnum

from app.adapters.base import Classification, Verdict, VerdictReason
from app.config import Settings


class Outcome(StrEnum):
    ACCEPT = "accept"
    # The rider inspects the plastic and decides.
    DEFER = "defer"
    REJECT = "reject"


@dataclass(frozen=True)
class Policy:
    version: str
    accept_at: float
    reject_at: float


def policy(settings: Settings) -> Policy:
    return Policy(
        version=settings.verification_policy_version,
        accept_at=settings.verify_accept_at,
        reject_at=settings.verify_reject_at,
    )


def decide(result: Classification, rules: Policy) -> Outcome:
    if result.reason is VerdictReason.DUPLICATE:
        return Outcome.REJECT
    # A verdict that contradicts its own reason goes to the rider, not through.
    if (
        result.verdict is Verdict.ACCEPT
        and result.reason is VerdictReason.OK
        and result.confidence >= rules.accept_at
    ):
        return Outcome.ACCEPT
    if (
        result.verdict is Verdict.REJECT
        and result.reason is not VerdictReason.OK
        and result.confidence >= rules.reject_at
    ):
        return Outcome.REJECT
    return Outcome.DEFER
