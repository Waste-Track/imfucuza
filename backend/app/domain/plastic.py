"""Plastic collection (engine-design.md sections 1 and 5). The rider weighs
and photographs the plastic, the classifier and the Engine's policy decide,
and the household's PIN turns an accepted check into points and rider pay.

Nothing earns pay automatically on a check the Engine can't trust: an
unusable reply, a photo that may be reused, a heavy load, a rider overriding
the check or not answering all send the pickup to a supervisor."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from app import jobs, services
from app.adapters.base import (
    ClassifierUnavailable,
    PhotoUnreadable,
    ProviderError,
    Verdict,
    VerifyRequest,
)
from app.config import get_settings
from app.db import Conn
from app.domain import events, ledger, money, notify, pickup_flow, pickups, review
from app.domain import verification as policy
from app.domain.photos import FLAT_BITS, PreparedPhoto, has_signal
from app.domain.pickups import Event, Offering, Status

# The first photo plus two retakes.
MAX_PHOTOS = 3
# Times a feature-phone rider can enter the weight for one pickup. Each one
# texts the household, so a rider can't spam them or keep a job open forever.
MAX_WEIGHT_ENTRIES = 3
# Photos this close to an earlier pickup's photo might be the same one reused.
NEAR_DUPLICATE_BITS = 8
PHOTO_URL_TTL_S = 300

# Why the plastic wasn't accepted, as the household is told.
REJECTED_BECAUSE = {
    "duplicate": "the photo was already used for another pickup",
    "contaminated": "it was mixed with other waste",
    "wrong_type": "it isn't a plastic we collect",
    "rider": "your rider checked it and it didn't qualify",
    "supervisor": "a supervisor checked the photo and it didn't qualify",
}


class WeightOutOfRange(ValueError):
    pass


class NothingToDecide(Exception):
    """The pickup isn't waiting for a supervisor's check."""


def kg(weight_g: int) -> str:
    """The exact weight in kg, as everyone involved sees it: 4.25, not 4.2."""
    return format(Decimal(weight_g) / 1000, "f")


def earnings(declared_type: str, weight_g: int) -> tuple[int, int]:
    """Points for the household and pay for the rider: floor(kg x rate)."""
    settings = get_settings()
    points = weight_g * settings.points_per_kg[declared_type] // 1000
    pay = weight_g * settings.rider_pay_pesewas_per_kg // 1000
    return points, pay


async def expect_rider_photo(conn: Conn, pickup_id: UUID, rider_id: UUID, weight_g: int) -> None:
    """Check an app rider can send the photo and weight now, before the upload
    is processed."""
    await _rider_may_submit(conn, pickup_id, rider_id)
    _check_weight(weight_g)


async def expect_household_photo(
    conn: Conn, pickup_id: UUID, household_id: UUID, weight_g: int
) -> None:
    await _household_may_submit(conn, pickup_id, household_id, weight_g)


async def enter_weight(
    conn: Conn, pickup_id: UUID, *, rider_id: UUID, user_id: UUID, weight_g: int
) -> None:
    """A feature-phone rider weighs the plastic by USSD. The household then
    takes the photo, since the rider's phone can't."""
    await pickup_flow.assigned_to(conn, pickup_id, rider_id)
    pickup = await _pickup(conn, pickup_id)
    _at_the_door(pickup)
    if pickup["rider_channel"] != "ussd":
        raise pickups.InvalidTransition("send the weight with the photo in the app")
    _check_weight(weight_g)
    cur = await conn.execute(
        "select count(*) as n from engine.events"
        " where pickup_id = %s and name = 'pickup.weight_entered'",
        (pickup_id,),
    )
    if (await cur.fetchone())["n"] >= MAX_WEIGHT_ENTRIES:
        raise pickups.InvalidTransition("the weight can't be changed again: call a supervisor")
    points, pay = earnings(pickup["declared_type"], weight_g)
    result = await pickups.apply(
        conn,
        pickup_id,
        Event.WEIGHT_ENTERED,
        expected_version=pickup["version"],
        actor_type="rider",
        actor_id=user_id,
        changes={"weight_g": weight_g, "points_due": points, "rider_pay_pesewas": pay},
        payload={"weight_g": weight_g, "points_due": points, "rider_pay_pesewas": pay},
    )
    await pickup_flow.watch_arrival(conn, pickup_id, result.version, since=pickup["arrived_at"])
    if weight_g != pickup["weight_g"]:
        await _tell(
            conn,
            pickup["household_user_id"],
            pickup_id,
            "photo_wanted",
            f"Imfucuza: your rider weighed {kg(weight_g)} kg of plastic. If that's right, take "
            "a photo of it now in the Imfucuza app so it can be checked.",
        )


async def submit_from_rider(
    conn: Conn,
    pickup_id: UUID,
    *,
    rider_id: UUID,
    user_id: UUID,
    photo_id: UUID,
    storage_key: str,
    photo: PreparedPhoto,
    weight_g: int,
) -> pickups.Transitioned:
    pickup = await _rider_may_submit(conn, pickup_id, rider_id)
    _check_weight(weight_g)
    return await _record(conn, pickup, "rider", user_id, photo_id, storage_key, photo, weight_g)


async def submit_from_household(
    conn: Conn,
    pickup_id: UUID,
    *,
    household_id: UUID,
    user_id: UUID,
    photo_id: UUID,
    storage_key: str,
    photo: PreparedPhoto,
    weight_g: int,
) -> pickups.Transitioned:
    pickup = await _household_may_submit(conn, pickup_id, household_id, weight_g)
    return await _record(
        conn, pickup, "household", user_id, photo_id, storage_key, photo, pickup["weight_g"]
    )


async def _rider_may_submit(conn: Conn, pickup_id: UUID, rider_id: UUID) -> dict:
    pickup = await pickup_flow.assigned_to(conn, pickup_id, rider_id)
    _at_the_door(pickup)
    return await _pickup(conn, pickup_id)


async def _household_may_submit(
    conn: Conn, pickup_id: UUID, household_id: UUID, weight_g: int
) -> dict:
    """`weight_g` is the weight the household was shown: the photo vouches for
    that weight only, not one the rider changed while it uploaded."""
    pickup = await _pickup(conn, pickup_id)
    if pickup["household_id"] != household_id:
        raise pickup_flow.NotYourPickup("pickup not found")
    _at_the_door(pickup)
    if pickup["rider_channel"] != "ussd":
        raise pickups.InvalidTransition("your rider takes the photo for this pickup")
    if pickup["weight_g"] is None:
        raise pickups.InvalidTransition("wait for your rider to weigh the plastic first")
    if pickup["weight_g"] != weight_g:
        raise pickups.InvalidTransition(
            f"the weight is now {kg(pickup['weight_g'])} kg: check it, then send the photo again"
        )
    return pickup


def _at_the_door(pickup: dict) -> None:
    if pickup["offering"] != Offering.PLASTIC or pickup["status"] != Status.ARRIVED:
        raise pickups.InvalidTransition(
            "the plastic is weighed and photographed once the rider has arrived"
        )


def _check_weight(weight_g: int) -> None:
    limit = get_settings().plastic_max_weight_g
    if not 0 < weight_g <= limit:
        raise WeightOutOfRange(f"the weight must be more than 0 and at most {limit // 1000} kg")


async def _record(
    conn: Conn,
    pickup: dict,
    source: str,
    user_id: UUID,
    photo_id: UUID,
    storage_key: str,
    photo: PreparedPhoto,
    weight_g: int,
) -> pickups.Transitioned:
    points, pay = earnings(pickup["declared_type"], weight_g)
    await conn.execute(
        """
        insert into engine.pickup_photos
            (id, pickup_id, storage_key, sha256, upload_sha256, dhash, source, captured_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            photo_id,
            pickup["id"],
            storage_key,
            photo.sha256,
            photo.upload_sha256,
            photo.dhash,
            source,
            user_id,
        ),
    )
    result = await pickups.apply(
        conn,
        pickup["id"],
        Event.PHOTO_SUBMITTED,
        expected_version=pickup["version"],
        actor_type=source,
        actor_id=user_id,
        changes={
            "weight_g": weight_g,
            "points_due": points,
            "rider_pay_pesewas": pay,
            "verification_outcome": "pending",
        },
        payload={
            "photo_id": str(photo_id),
            "weight_g": weight_g,
            "points_due": points,
            "rider_pay_pesewas": pay,
        },
    )
    await jobs.enqueue(
        conn,
        "verification.run",
        {"pickup_id": str(pickup["id"]), "photo_id": str(photo_id)},
        dedupe_key=f"verify:{photo_id}",
    )
    return result


# The check -------------------------------------------------------------------


async def _verification_gave_up(conn: Conn, payload: dict, error: str) -> None:
    pickup = await _pickup(conn, UUID(payload["pickup_id"]))
    if pickup["status"] == Status.VERIFYING:
        await _to_supervisor(conn, pickup, "the photo couldn't be checked")


@jobs.handler("verification.run", on_give_up=_verification_gave_up)
async def _run(conn: Conn, payload: dict[str, Any]) -> None:
    pickup = await _pickup(conn, UUID(payload["pickup_id"]))
    if pickup["status"] != Status.VERIFYING:
        return
    cur = await conn.execute(
        "select * from engine.pickup_photos where id = %s", (payload["photo_id"],)
    )
    photo = await cur.fetchone()

    copy = await _earlier_copy(conn, photo)
    if copy:
        await events.record(
            conn,
            "photo.duplicate",
            actor_type="system",
            pickup_id=pickup["id"],
            payload={"photo_id": str(photo["id"]), "of": str(copy["id"]), "exact": copy["exact"]},
        )
        if copy["exact"]:
            await _log(conn, pickup, photo["id"], "policy", "reject", reason="duplicate")
            await _reject(conn, pickup, "duplicate", Event.VERIFICATION_REJECTED)
        else:
            await _to_supervisor(conn, pickup, "the photo may have been used before")
        return

    svc = services.get()
    try:
        url = await svc.photos.signed_url(photo["storage_key"], PHOTO_URL_TTL_S)
    except ProviderError:
        # Don't keep the rider waiting at the door for storage to recover.
        await _to_supervisor(conn, pickup, "the photo couldn't be read from storage")
        return
    try:
        result = await svc.classifier.verify(
            VerifyRequest(
                verification_id=photo["id"],
                pickup_id=pickup["id"],
                declared_type=pickup["declared_type"],
                photo_url=url,
                photo_sha256=photo["sha256"],
                captured_at=photo["created_at"],
                capture_source=photo["source"],
            )
        )
    except PhotoUnreadable:
        await _log(conn, pickup, photo["id"], "classifier", "unreadable")
        await _retake(conn, pickup, photo["source"])
        return
    except ClassifierUnavailable as exc:
        await _log(conn, pickup, photo["id"], "classifier", "unavailable", reason=str(exc)[:200])
        await events.record(
            conn, "verification.unavailable", actor_type="system", pickup_id=pickup["id"]
        )
        await _to_supervisor(
            conn, pickup, "the photo checker was unavailable", Event.VERIFICATION_UNAVAILABLE
        )
        return

    rules = policy.policy(get_settings())
    outcome = policy.decide(result, rules)
    await _log(
        conn,
        pickup,
        photo["id"],
        "classifier",
        result.verdict,
        confidence=result.confidence,
        reason=result.reason,
        model_version=result.model_version,
        latency_ms=result.latency_ms,
    )
    await _log(
        conn,
        pickup,
        photo["id"],
        "policy",
        outcome,
        reason=result.reason,
        policy_version=rules.version,
    )
    await events.record(
        conn,
        "verification.classified",
        actor_type="system",
        pickup_id=pickup["id"],
        payload={
            "declared_type": pickup["declared_type"],
            "verdict": result.verdict,
            "confidence": result.confidence,
            "reason": result.reason,
            "model_version": result.model_version,
            "outcome": outcome,
        },
    )
    if outcome is policy.Outcome.ACCEPT and photo["source"] == "household":
        # The household took the photo that earns it points, and a feature-phone
        # rider can't see it: the rider vouches for the plastic in hand.
        await _defer(conn, pickup, passed=True)
    elif outcome is policy.Outcome.ACCEPT:
        await _accept(conn, pickup, Event.VERIFICATION_ACCEPTED, actor_type="system")
    elif outcome is policy.Outcome.REJECT:
        await _reject(conn, pickup, result.reason, Event.VERIFICATION_REJECTED)
    else:
        await _defer(conn, pickup)


async def _earlier_copy(conn: Conn, photo: dict) -> dict | None:
    """The same photo, or one very like it, sent for any other pickup."""
    cur = await conn.execute(
        """
        select id, (sha256 = %(sha)s or upload_sha256 = %(upload)s) as exact,
               bit_count((dhash # %(dhash)s)::bit(64)) as bits
          from engine.pickup_photos
         where pickup_id <> %(pickup)s
           and (sha256 = %(sha)s or upload_sha256 = %(upload)s
                or (%(near)s and bit_count((dhash # %(dhash)s)::bit(64)) <= %(bits)s
                    and bit_count(dhash::bit(64)) between %(flat)s and 64 - %(flat)s))
         order by exact desc, bits
         limit 1
        """,
        {
            "sha": photo["sha256"],
            "upload": photo["upload_sha256"],
            "dhash": photo["dhash"],
            "pickup": photo["pickup_id"],
            "near": has_signal(photo["dhash"]),
            "bits": NEAR_DUPLICATE_BITS,
            "flat": FLAT_BITS,
        },
    )
    return await cur.fetchone()


async def _retake(conn: Conn, pickup: dict, taken_by: str) -> None:
    cur = await conn.execute(
        "select count(*) as n from engine.pickup_photos where pickup_id = %s", (pickup["id"],)
    )
    if (await cur.fetchone())["n"] >= MAX_PHOTOS:
        await _to_supervisor(conn, pickup, "no usable photo after retakes")
        return
    result = await pickups.apply(
        conn,
        pickup["id"],
        Event.PHOTO_UNREADABLE,
        expected_version=pickup["version"],
        actor_type="system",
    )
    await pickup_flow.watch_arrival(conn, pickup["id"], result.version, since=pickup["arrived_at"])
    await _tell(
        conn,
        pickup["household_user_id"] if taken_by == "household" else pickup["rider_user_id"],
        pickup["id"],
        "retake",
        "Imfucuza: the photo of the plastic couldn't be used. Please take another in the app.",
    )
    if taken_by == "household":
        await _tell(
            conn,
            pickup["rider_user_id"],
            pickup["id"],
            "retake",
            "Imfucuza: the household's photo couldn't be used, so they are taking another. "
            "Please wait.",
        )


async def _accept(
    conn: Conn, pickup: dict, event: Event, *, actor_type: str, actor_id: UUID | None = None
) -> None:
    settings = get_settings()
    cur = await conn.execute(
        """
        select source from engine.pickup_photos where pickup_id = %s
         order by created_at desc limit 1
        """,
        (pickup["id"],),
    )
    latest = await cur.fetchone()
    heavy = (
        settings.plastic_household_review_above_g
        if latest and latest["source"] == "household"
        else settings.plastic_review_above_g
    )
    if pickup["weight_g"] > heavy:
        await _to_supervisor(conn, pickup, "a heavy load: check the weight against the photo")
        return
    result = await pickups.apply(
        conn,
        pickup["id"],
        event,
        expected_version=pickup["version"],
        actor_type=actor_type,
        actor_id=actor_id,
        changes={"verification_outcome": "accepted", "collected_at": pickups.NOW},
    )
    await pickup_flow.await_pin(conn, pickup["id"], result.version)
    if actor_type != "rider":
        await _tell_ussd_rider_to_collect(conn, pickup, "The plastic was accepted.")


async def _reject(
    conn: Conn,
    pickup: dict,
    because: str,
    event: Event,
    *,
    actor_type: str = "system",
    actor_id: UUID | None = None,
) -> None:
    await pickups.apply(
        conn,
        pickup["id"],
        event,
        expected_version=pickup["version"],
        actor_type=actor_type,
        actor_id=actor_id,
        changes={"verification_outcome": "rejected", "failure_reason": because},
    )
    await _tell(
        conn,
        pickup["household_user_id"],
        pickup["id"],
        "plastic_rejected",
        f"Imfucuza: your plastic wasn't accepted because {REJECTED_BECAUSE[because]}. "
        "It earns no points this time.",
    )
    if actor_type != "rider":
        await _tell(
            conn,
            pickup["rider_user_id"],
            pickup["id"],
            "plastic_rejected",
            "Imfucuza: the plastic at your current pickup wasn't accepted. Leave it with the "
            "household. The job is closed.",
        )


async def _defer(conn: Conn, pickup: dict, *, passed: bool = False) -> None:
    result = await pickups.apply(
        conn,
        pickup["id"],
        Event.VERIFICATION_DEFERRED,
        expected_version=pickup["version"],
        actor_type="system",
        changes={"verification_outcome": "deferred"},
    )
    wait_s = get_settings().rider_review_s
    await jobs.enqueue(
        conn,
        "verification.rider_timeout",
        {"pickup_id": str(pickup["id"]), "version": result.version},
        run_at=datetime.now(UTC) + timedelta(seconds=wait_s),
        dedupe_key=f"rider-review:{pickup['id']}:{result.version}",
    )
    where = (
        f"dial {_ussd_code()}, choose 5 and accept or reject it"
        if pickup["rider_channel"] == "ussd"
        else "accept or reject it in the app"
    )
    check = (
        "the household's photo passed the check. Make sure the plastic in front of you matches"
        if passed
        else "the photo check was unsure. Look at the plastic"
    )
    await _tell(
        conn,
        pickup["rider_user_id"],
        pickup["id"],
        "rider_review",
        f"Imfucuza: {check}, then {where} within {wait_s // 60} min.",
    )


async def _to_supervisor(
    conn: Conn, pickup: dict, reason: str, event: Event = Event.SENT_FOR_REVIEW
) -> None:
    """Collect it anyway: the household gets its PIN, its points wait for a
    supervisor to check the photo, and the rider's pay waits too."""
    result = await pickups.apply(
        conn,
        pickup["id"],
        event,
        expected_version=pickup["version"],
        actor_type="system",
        changes={"verification_outcome": "manual_review", "collected_at": pickups.NOW},
    )
    await review.open_item(
        conn,
        "manual_verification",
        pickup_id=pickup["id"],
        household_id=pickup["household_id"],
        payload={"reason": reason},
    )
    await pickup_flow.await_pin(conn, pickup["id"], result.version)
    await _tell_ussd_rider_to_collect(conn, pickup, "A supervisor will check the photo.")


# The rider decides -------------------------------------------------------------


async def rider_decides(
    conn: Conn, pickup_id: UUID, *, rider_id: UUID, user_id: UUID, accept: bool
) -> str:
    await pickup_flow.assigned_to(conn, pickup_id, rider_id)
    pickup = await _pickup(conn, pickup_id)
    if pickup["status"] != Status.RIDER_REVIEW:
        raise pickups.InvalidTransition("this pickup isn't waiting for your decision")
    cur = await conn.execute(
        """
        select decision, photo_id from engine.verification_decisions
         where pickup_id = %s and stage = 'classifier' order by id desc limit 1
        """,
        (pickup_id,),
    )
    leaning = await cur.fetchone() or {"decision": None, "photo_id": None}
    # Going against what the classifier leant towards, below its thresholds.
    overrides = (accept and leaning["decision"] == Verdict.REJECT) or (
        not accept and leaning["decision"] == Verdict.ACCEPT
    )
    decision = "accept" if accept else "reject"
    await _log(
        conn,
        pickup,
        leaning["photo_id"],
        "rider",
        decision,
        actor_id=user_id,
        is_override=overrides,
    )
    await events.record(
        conn,
        "verification.rider_decided",
        actor_type="rider",
        actor_id=user_id,
        pickup_id=pickup_id,
        payload={"decision": decision, "is_override": overrides},
    )
    if accept and overrides:
        # Riders are paid for what they accept, so an override waits for a
        # person until override rates are monitored (engine-design.md section 6).
        await _to_supervisor(
            conn, pickup, "the rider accepted plastic the check leant towards rejecting"
        )
        return Status.AWAITING_PIN
    if accept:
        await _accept(conn, pickup, Event.RIDER_ACCEPTED, actor_type="rider", actor_id=user_id)
        return Status.AWAITING_PIN
    await _reject(conn, pickup, "rider", Event.RIDER_REJECTED, actor_type="rider", actor_id=user_id)
    return Status.REJECTED


async def _rider_timeout_gave_up(conn: Conn, payload: dict, error: str) -> None:
    pickup = await _pickup(conn, UUID(payload["pickup_id"]))
    if pickup["status"] == Status.RIDER_REVIEW:
        await _to_supervisor(conn, pickup, "the rider's decision couldn't be timed out")


@jobs.handler("verification.rider_timeout", on_give_up=_rider_timeout_gave_up)
async def _rider_timeout(conn: Conn, payload: dict[str, Any]) -> None:
    pickup = await _pickup(conn, UUID(payload["pickup_id"]))
    if pickup["status"] == Status.RIDER_REVIEW and pickup["version"] == payload["version"]:
        await _to_supervisor(
            conn, pickup, "the rider didn't decide in time", Event.RIDER_REVIEW_TIMED_OUT
        )


# Points and pay ----------------------------------------------------------------


async def award(conn: Conn, pickup_id: UUID) -> None:
    """At PIN confirmation, in its transaction. Accepted plastic earns now.
    Plastic waiting for a supervisor earns pending points and no pay yet."""
    pickup = await _pickup(conn, pickup_id)
    points, pay = pickup["points_due"] or 0, pickup["rider_pay_pesewas"] or 0
    outcome = pickup["verification_outcome"]
    if outcome not in ("accepted", "manual_review"):
        return
    if points:
        await ledger.post(
            conn,
            money.points_accrue(
                pickup_id, pickup["household_id"], points, pending=outcome == "manual_review"
            ),
        )
    if pay and outcome == "accepted":
        await ledger.post(conn, money.rider_pay(pickup_id, pickup["assigned_rider_id"], pay))


async def balance(conn: Conn, household_id: UUID) -> dict[str, int]:
    available = await ledger.balance(conn, ledger.Account(ledger.POINTS_AVAILABLE, household_id))
    pending = await ledger.balance(conn, ledger.Account(ledger.POINTS_PENDING, household_id))
    return {"available": available, "pending": pending}


# A supervisor decides ------------------------------------------------------------


async def supervisor_decides(
    conn: Conn, pickup_id: UUID, *, supervisor_id: UUID, accept: bool, note: str
) -> str:
    # PINs before the pickup, the order a confirm takes them in.
    cur = await conn.execute(
        "select id from engine.pins where pickup_id = %s and status in ('active', 'locked')"
        " for update",
        (pickup_id,),
    )
    live_pins = [row["id"] for row in await cur.fetchall()]
    await conn.execute(
        "select id from engine.pickup_requests where id = %s for no key update", (pickup_id,)
    )
    pickup = await _pickup(conn, pickup_id)
    if pickup["verification_outcome"] != "manual_review" or pickup["status"] not in (
        Status.AWAITING_PIN,
        Status.COMPLETED,
    ):
        raise NothingToDecide("this pickup isn't waiting for a supervisor's check")
    cur = await conn.execute(
        """
        select id from engine.review_items
         where pickup_id = %s and type = 'manual_verification' and status = 'open'
           and assigned_to is not null and assigned_to <> %s
           and claimed_at > now() - %s
        """,
        (pickup_id, supervisor_id, review.STALE_CLAIM),
    )
    if await cur.fetchone():
        raise NothingToDecide("another supervisor has claimed this check")
    cur = await conn.execute(
        """
        select id from engine.pickup_photos where pickup_id = %s
         order by created_at desc limit 1
        """,
        (pickup_id,),
    )
    photo = await cur.fetchone()
    decision = "accept" if accept else "reject"
    await _log(
        conn,
        pickup,
        photo["id"] if photo else None,
        "supervisor",
        decision,
        actor_id=supervisor_id,
        reason=note,
    )

    points, pay = pickup["points_due"] or 0, pickup["rider_pay_pesewas"] or 0
    if pickup["status"] == Status.COMPLETED:
        if accept:
            if points:
                await ledger.post(
                    conn, money.points_release(pickup_id, pickup["household_id"], points)
                )
            if pay:
                await ledger.post(
                    conn, money.rider_pay(pickup_id, pickup["assigned_rider_id"], pay)
                )
        elif points:
            await ledger.post(conn, money.points_void(pickup_id, pickup["household_id"], points))
        await _set_outcome(conn, pickup, "accepted" if accept else "rejected", supervisor_id)
    elif accept:
        await _set_outcome(conn, pickup, "accepted", supervisor_id)
    else:
        await conn.execute(
            "update engine.pins set status = 'expired' where id = any(%s)", (live_pins,)
        )
        await pickups.apply(
            conn,
            pickup_id,
            Event.MATERIAL_REJECTED,
            expected_version=pickup["version"],
            actor_type="supervisor",
            actor_id=supervisor_id,
            changes={"verification_outcome": "rejected", "failure_reason": "supervisor"},
        )
        await _tell(
            conn,
            pickup["rider_user_id"],
            pickup_id,
            "plastic_rejected",
            "Imfucuza: a supervisor didn't accept the plastic at your pickup. If you have it, "
            "return it to the household. The job is closed.",
        )

    if accept:
        await _tell(
            conn,
            pickup["household_user_id"],
            pickup_id,
            "plastic_checked",
            f"Imfucuza: a supervisor checked your plastic and it qualifies for {points} points. "
            + (
                "They are now in your balance."
                if pickup["status"] == Status.COMPLETED
                else "Confirm the pickup with your PIN to get them."
            ),
        )
    else:
        await _tell(
            conn,
            pickup["household_user_id"],
            pickup_id,
            "plastic_rejected",
            f"Imfucuza: your plastic wasn't accepted because {REJECTED_BECAUSE['supervisor']}. "
            "It earns no points this time.",
        )
    cur = await conn.execute(
        """
        update engine.review_items
           set status = 'resolved', resolved_at = now(), resolved_by = %s, resolution = %s,
               assigned_to = %s, claimed_at = coalesce(claimed_at, now())
         where pickup_id = %s and type = 'manual_verification' and status = 'open'
        returning id
        """,
        (supervisor_id, note, supervisor_id, pickup_id),
    )
    for item in await cur.fetchall():
        await events.record(
            conn,
            "review.resolved",
            actor_type="supervisor",
            actor_id=supervisor_id,
            pickup_id=pickup_id,
            payload={"review_item_id": str(item["id"]), "type": "manual_verification"},
        )
    return decision


async def _set_outcome(conn: Conn, pickup: dict, outcome: str, supervisor_id: UUID) -> None:
    await conn.execute(
        """
        update engine.pickup_requests
           set verification_outcome = %s, version = version + 1, updated_at = now()
         where id = %s
        """,
        (outcome, pickup["id"]),
    )
    await events.record(
        conn,
        "verification.supervisor_decided",
        actor_type="supervisor",
        actor_id=supervisor_id,
        pickup_id=pickup["id"],
        payload={"outcome": outcome},
    )


# Helpers -----------------------------------------------------------------------


async def _log(
    conn: Conn,
    pickup: dict,
    photo_id: UUID | None,
    stage: str,
    decision: str,
    *,
    confidence: float | None = None,
    reason: str | None = None,
    model_version: str | None = None,
    policy_version: str | None = None,
    latency_ms: int | None = None,
    actor_id: UUID | None = None,
    is_override: bool | None = None,
) -> None:
    await conn.execute(
        """
        insert into engine.verification_decisions
            (pickup_id, photo_id, stage, decision, confidence, reason, model_version,
             policy_version, latency_ms, actor_id, is_override)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            pickup["id"],
            photo_id,
            stage,
            str(decision),
            confidence,
            None if reason is None else str(reason),
            model_version,
            policy_version,
            latency_ms,
            actor_id,
            is_override,
        ),
    )


async def _tell_ussd_rider_to_collect(conn: Conn, pickup: dict, outcome: str) -> None:
    """App riders see the next step in the app. Feature-phone riders need telling."""
    if pickup["rider_channel"] == "ussd":
        await _tell(
            conn,
            pickup["rider_user_id"],
            pickup["id"],
            "collect_plastic",
            f"Imfucuza: {outcome} Take the plastic, ask the household for the PIN, then dial "
            f"{_ussd_code()} and choose 6.",
        )


def _ussd_code() -> str:
    return get_settings().ussd_code or "the Imfucuza code"


async def _tell(
    conn: Conn, user_id: UUID | None, pickup_id: UUID, template: str, text: str
) -> None:
    if user_id:
        await notify.queue_sms(
            conn, user_id=user_id, pickup_id=pickup_id, template=template, text=text
        )


async def _pickup(conn: Conn, pickup_id: UUID) -> dict[str, Any]:
    cur = await conn.execute(
        """
        select p.*, h.user_id as household_user_id, r.user_id as rider_user_id,
               r.channel as rider_channel
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
          left join engine.riders r on r.id = p.assigned_rider_id
         where p.id = %s
        """,
        (pickup_id,),
    )
    pickup = await cur.fetchone()
    if pickup is None:
        raise pickup_flow.NotYourPickup("pickup not found")
    return pickup
