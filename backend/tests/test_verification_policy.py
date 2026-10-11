from uuid import uuid4

import pytest

from app.adapters.base import Classification, Verdict, VerdictReason
from app.config import Settings
from app.domain.verification import Outcome, Policy, decide, policy

RULES = Policy(version="test", accept_at=0.90, reject_at=0.80)


def result(verdict: Verdict, confidence: float, reason: VerdictReason) -> Classification:
    return Classification(uuid4(), verdict, confidence, reason, "cls-test", 10)


@pytest.mark.parametrize(
    ("verdict", "confidence", "reason", "outcome"),
    [
        (Verdict.ACCEPT, 0.90, VerdictReason.OK, Outcome.ACCEPT),
        (Verdict.ACCEPT, 0.89, VerdictReason.OK, Outcome.DEFER),
        (Verdict.REJECT, 0.80, VerdictReason.CONTAMINATED, Outcome.REJECT),
        (Verdict.REJECT, 0.79, VerdictReason.WRONG_TYPE, Outcome.DEFER),
        (Verdict.UNCERTAIN, 0.99, VerdictReason.OK, Outcome.DEFER),
        # A duplicate is refused however unsure the classifier is.
        (Verdict.UNCERTAIN, 0.10, VerdictReason.DUPLICATE, Outcome.REJECT),
        (Verdict.ACCEPT, 0.99, VerdictReason.DUPLICATE, Outcome.REJECT),
        # Answers that contradict themselves go to the rider.
        (Verdict.ACCEPT, 0.99, VerdictReason.CONTAMINATED, Outcome.DEFER),
        (Verdict.REJECT, 0.99, VerdictReason.OK, Outcome.DEFER),
    ],
)
def test_decision_policy(verdict, confidence, reason, outcome):
    assert decide(result(verdict, confidence, reason), RULES) is outcome


def test_policy_comes_from_settings():
    rules = policy(Settings(verify_accept_at=0.95, verify_reject_at=0.7))

    assert (rules.accept_at, rules.reject_at, rules.version) == (0.95, 0.7, "2026-10-p1")
