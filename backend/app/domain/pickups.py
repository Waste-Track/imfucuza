"""Pickup lifecycle (engine-design.md section 1).

The transition table is the single source of truth for which status changes
are legal. `apply` performs one change with optimistic locking and records it
in the event log.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from psycopg import sql

from app.db import Conn
from app.domain import events


class Offering(StrEnum):
    REFUSE = "refuse"
    PLASTIC = "plastic"


class Status(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    PENDING_DISPATCH = "pending_dispatch"
    OFFERED = "offered"
    ASSIGNED = "assigned"
    ARRIVED = "arrived"
    LOCATION_ISSUE = "location_issue"
    VERIFYING = "verifying"
    RIDER_REVIEW = "rider_review"
    AWAITING_PIN = "awaiting_pin"
    DISPUTED = "disputed"
    COMPLETED = "completed"
    REJECTED = "rejected"
    UNCONFIRMED = "unconfirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
    REFUNDED = "refunded"


class Event(StrEnum):
    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_FAILED = "payment_failed"
    OFFER_SENT = "offer_sent"
    OFFER_ACCEPTED = "offer_accepted"
    # Declined, expired or undeliverable: the request goes back for dispatch.
    OFFER_RELEASED = "offer_released"
    DISPATCH_WINDOW_ENDED = "dispatch_window_ended"
    CANCELLED_BY_HOUSEHOLD = "cancelled_by_household"
    RIDER_ARRIVED = "rider_arrived"
    LOCATION_ISSUE_REPORTED = "location_issue_reported"
    LOCATION_ISSUE_RESOLVED = "location_issue_resolved"
    LOCATION_ISSUE_ABANDONED = "location_issue_abandoned"
    REFUSE_COLLECTED = "refuse_collected"
    WRONG_MATERIAL = "wrong_material"
    PHOTO_SUBMITTED = "photo_submitted"
    PHOTO_UNREADABLE = "photo_unreadable"
    VERIFICATION_ACCEPTED = "verification_accepted"
    VERIFICATION_DEFERRED = "verification_deferred"
    VERIFICATION_REJECTED = "verification_rejected"
    # Classifier down or rider review timed out: collect, and a supervisor decides.
    VERIFICATION_UNAVAILABLE = "verification_unavailable"
    RIDER_ACCEPTED = "rider_accepted"
    RIDER_REJECTED = "rider_rejected"
    RIDER_REVIEW_TIMED_OUT = "rider_review_timed_out"
    PIN_CONFIRMED = "pin_confirmed"
    PROBLEM_REPORTED = "problem_reported"
    PIN_EXPIRED = "pin_expired"
    DISPUTE_PIN_REISSUED = "dispute_pin_reissued"
    DISPUTE_REFUNDED = "dispute_refunded"
    SUPERVISOR_ASSIGNED = "supervisor_assigned"
    SUPERVISOR_REDISPATCHED = "supervisor_redispatched"
    CANCELLED_BY_SUPERVISOR = "cancelled_by_supervisor"


BOTH = frozenset(Offering)
REFUSE = frozenset({Offering.REFUSE})
PLASTIC = frozenset({Offering.PLASTIC})

DISPATCH_STAGE = (Status.PENDING_DISPATCH, Status.OFFERED, Status.ASSIGNED)
SUPERVISOR_CANCELLABLE = (
    Status.PENDING_DISPATCH,
    Status.OFFERED,
    Status.ASSIGNED,
    Status.ARRIVED,
    Status.LOCATION_ISSUE,
    Status.AWAITING_PIN,
)

# (from statuses, event, to status, offerings it applies to)
_RULES: list[tuple[tuple[Status, ...], Event, Status, frozenset[Offering]]] = [
    ((Status.AWAITING_PAYMENT,), Event.PAYMENT_SUCCEEDED, Status.PENDING_DISPATCH, REFUSE),
    ((Status.AWAITING_PAYMENT,), Event.PAYMENT_FAILED, Status.CANCELLED, REFUSE),
    ((Status.PENDING_DISPATCH,), Event.OFFER_SENT, Status.OFFERED, BOTH),
    ((Status.PENDING_DISPATCH, Status.OFFERED), Event.DISPATCH_WINDOW_ENDED, Status.EXPIRED, BOTH),
    ((Status.OFFERED,), Event.OFFER_ACCEPTED, Status.ASSIGNED, BOTH),
    ((Status.OFFERED,), Event.OFFER_RELEASED, Status.PENDING_DISPATCH, BOTH),
    ((Status.AWAITING_PAYMENT,), Event.CANCELLED_BY_HOUSEHOLD, Status.CANCELLED, REFUSE),
    (DISPATCH_STAGE, Event.CANCELLED_BY_HOUSEHOLD, Status.CANCELLED, BOTH),
    ((Status.ASSIGNED,), Event.RIDER_ARRIVED, Status.ARRIVED, BOTH),
    ((Status.ASSIGNED, Status.ARRIVED), Event.LOCATION_ISSUE_REPORTED, Status.LOCATION_ISSUE, BOTH),
    ((Status.LOCATION_ISSUE,), Event.LOCATION_ISSUE_RESOLVED, Status.ARRIVED, BOTH),
    ((Status.LOCATION_ISSUE,), Event.LOCATION_ISSUE_ABANDONED, Status.FAILED, BOTH),
    ((Status.ARRIVED,), Event.REFUSE_COLLECTED, Status.AWAITING_PIN, REFUSE),
    ((Status.ARRIVED,), Event.WRONG_MATERIAL, Status.FAILED, REFUSE),
    ((Status.ARRIVED,), Event.PHOTO_SUBMITTED, Status.VERIFYING, PLASTIC),
    ((Status.VERIFYING,), Event.PHOTO_UNREADABLE, Status.ARRIVED, PLASTIC),
    ((Status.VERIFYING,), Event.VERIFICATION_ACCEPTED, Status.AWAITING_PIN, PLASTIC),
    ((Status.VERIFYING,), Event.VERIFICATION_DEFERRED, Status.RIDER_REVIEW, PLASTIC),
    ((Status.VERIFYING,), Event.VERIFICATION_REJECTED, Status.REJECTED, PLASTIC),
    ((Status.VERIFYING,), Event.VERIFICATION_UNAVAILABLE, Status.AWAITING_PIN, PLASTIC),
    ((Status.RIDER_REVIEW,), Event.RIDER_ACCEPTED, Status.AWAITING_PIN, PLASTIC),
    ((Status.RIDER_REVIEW,), Event.RIDER_REJECTED, Status.REJECTED, PLASTIC),
    ((Status.RIDER_REVIEW,), Event.RIDER_REVIEW_TIMED_OUT, Status.AWAITING_PIN, PLASTIC),
    ((Status.AWAITING_PIN,), Event.PIN_CONFIRMED, Status.COMPLETED, BOTH),
    ((Status.AWAITING_PIN,), Event.PROBLEM_REPORTED, Status.DISPUTED, BOTH),
    ((Status.AWAITING_PIN,), Event.PIN_EXPIRED, Status.UNCONFIRMED, BOTH),
    ((Status.DISPUTED,), Event.DISPUTE_PIN_REISSUED, Status.AWAITING_PIN, BOTH),
    ((Status.DISPUTED,), Event.DISPUTE_REFUNDED, Status.REFUNDED, BOTH),
    ((Status.PENDING_DISPATCH,), Event.SUPERVISOR_ASSIGNED, Status.ASSIGNED, BOTH),
    ((Status.ASSIGNED,), Event.SUPERVISOR_REDISPATCHED, Status.PENDING_DISPATCH, BOTH),
    ((Status.AWAITING_PAYMENT,), Event.CANCELLED_BY_SUPERVISOR, Status.CANCELLED, REFUSE),
    (SUPERVISOR_CANCELLABLE, Event.CANCELLED_BY_SUPERVISOR, Status.CANCELLED, BOTH),
    (
        (Status.VERIFYING, Status.RIDER_REVIEW),
        Event.CANCELLED_BY_SUPERVISOR,
        Status.CANCELLED,
        PLASTIC,
    ),
]


def _build_transitions() -> dict[tuple[Offering, Status, Event], Status]:
    table: dict[tuple[Offering, Status, Event], Status] = {}
    for sources, event, target, offerings in _RULES:
        for offering in offerings:
            for source in sources:
                key = (offering, source, event)
                if key in table:
                    raise RuntimeError(f"duplicate transition {key}")
                table[key] = target
    return table


TRANSITIONS = _build_transitions()

TERMINAL = frozenset(
    {
        Status.COMPLETED,
        Status.REJECTED,
        Status.UNCONFIRMED,
        Status.CANCELLED,
        Status.EXPIRED,
        Status.FAILED,
        Status.REFUNDED,
    }
)


# Pickup columns a transition may set alongside the status.
_UPDATABLE = frozenset(
    {
        "assigned_rider_id",
        "verification_outcome",
        "weight_g",
        "failure_reason",
        "paid_at",
        "first_offered_at",
        "assigned_at",
        "arrived_at",
        "collected_at",
        "completed_at",
    }
)
# Pass as a `changes` value to set a timestamp column to the database's now().
NOW = object()


def initial_status(offering: Offering) -> Status:
    return Status.AWAITING_PAYMENT if offering is Offering.REFUSE else Status.PENDING_DISPATCH


class InvalidTransition(Exception):
    pass


class StaleVersion(Exception):
    """Someone else changed the pickup first. Reload and decide again."""


def next_status(offering: Offering, current: Status, event: Event) -> Status:
    try:
        return TRANSITIONS[(offering, current, event)]
    except KeyError:
        raise InvalidTransition(
            f"{event} is not allowed for a {offering} pickup in {current}"
        ) from None


@dataclass(frozen=True)
class Transitioned:
    pickup_id: UUID
    previous: Status
    status: Status
    version: int


async def apply(
    conn: Conn,
    pickup_id: UUID,
    event: Event,
    *,
    expected_version: int,
    actor_type: str,
    actor_id: UUID | None = None,
    changes: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Transitioned:
    """Move a pickup to the status the transition table gives for `event`.

    `changes` sets other pickup columns in the same update. Their names are
    checked against an allow-list, never interpolated from user input.

    When a step also posts to the ledger, transition first and post second,
    so every path takes the pickup lock before any account lock.
    """
    if actor_type not in events.ACTOR_TYPES:
        raise ValueError(f"unknown actor type {actor_type!r}")
    extra = dict(changes or {})
    unknown = extra.keys() - _UPDATABLE
    if unknown:
        raise ValueError(f"cannot set {sorted(unknown)} through a transition")

    async with conn.transaction():
        # NO KEY UPDATE: rows referencing the pickup (events, ledger) can still
        # be inserted by others while we hold it.
        cur = await conn.execute(
            """
            select offering, status, version from engine.pickup_requests
             where id = %s for no key update
            """,
            (pickup_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"pickup {pickup_id} not found")
        if row["version"] != expected_version:
            raise StaleVersion(
                f"pickup {pickup_id} is at version {row['version']}, not {expected_version}"
            )

        current = Status(row["status"])
        target = next_status(Offering(row["offering"]), current, event)
        if target is Status.COMPLETED:
            extra.setdefault("completed_at", NOW)

        assignments = [sql.SQL("status = %(status)s, version = version + 1, updated_at = now()")]
        params: dict[str, Any] = {"status": target, "id": pickup_id}
        for column, value in extra.items():
            if value is NOW:
                assignments.append(sql.SQL("{} = now()").format(sql.Identifier(column)))
            else:
                assignments.append(
                    sql.SQL("{} = {}").format(sql.Identifier(column), sql.Placeholder(column))
                )
                params[column] = value

        query = sql.SQL(
            "update engine.pickup_requests set {} where id = %(id)s returning version"
        ).format(sql.SQL(", ").join(assignments))
        cur = await conn.execute(query, params)
        version = (await cur.fetchone())["version"]

        await events.record(
            conn,
            f"pickup.{event}",
            actor_type=actor_type,
            actor_id=actor_id,
            pickup_id=pickup_id,
            payload={**(payload or {}), "from": current, "to": target},
        )
    return Transitioned(pickup_id, current, target, version)
