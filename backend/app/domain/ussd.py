"""USSD menus for riders and households on feature phones (engine-design.md section 10).

Each keypress arrives as its own request. The session row remembers the
screen and anything chosen so far. Screens stay within 160 characters, the
most a network reliably shows on one page, and the handler only touches the
database: anything slower (SMS) goes through the job queue.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app import jobs
from app.adapters.ussd_base import UssdReply, UssdRequest
from app.config import get_settings
from app.db import Conn
from app.domain import (
    dispatch,
    events,
    notify,
    people,
    pickup_flow,
    pins,
    plastic,
    review,
    rewards,
)
from app.domain.pickups import InvalidTransition, StaleVersion, Status

log = logging.getLogger(__name__)

MAX_SCREEN = 160
# A session idle this long is over: a request carrying its id starts afresh.
SESSION_IDLE = "3 minutes"
SESSION_RETENTION = "30 days"
PAGE_SIZE = 5
NAME_WIDTH = 18

NOT_REGISTERED = (
    "This number is not registered with Imfucuza. Sign up in the app or ask a supervisor."
)
WRONG_NUMBER = "This session belongs to another phone. Please dial again."
SORRY = "Sorry, something went wrong. Please dial again."

OFFER = dispatch.USSD_OFFER_OPTION
RIDER_HOME = "\n".join(
    [
        "Imfucuza rider: {duty}",
        "1 {toggle}",
        "2 Update location",
        f"{OFFER} Job offer",
        "4 Arrived",
        "5 Collected",
        "6 Household PIN",
    ]
)
SHOP_HOME = "Imfucuza shop\n1 Redeem a voucher"
HOUSEHOLD_HOME = (
    "Imfucuza\n1 Confirm pickup with PIN\n2 Report a problem\n3 My last pickup\n4 My points"
)

STATUS_WORDS = {
    Status.AWAITING_PAYMENT: "waiting for payment",
    Status.PENDING_DISPATCH: "looking for a rider",
    Status.OFFERED: "looking for a rider",
    Status.ASSIGNED: "rider on the way",
    Status.ARRIVED: "rider has arrived",
    Status.LOCATION_ISSUE: "rider can't find you",
    Status.VERIFYING: "checking the plastic",
    Status.RIDER_REVIEW: "rider is checking the plastic",
    Status.AWAITING_PIN: "collected, confirm with your PIN",
    Status.DISPUTED: "problem reported, a supervisor will call",
    Status.COMPLETED: "done",
    Status.CANCELLED: "cancelled",
    Status.EXPIRED: "no rider was found, refunded",
    Status.UNCONFIRMED: "never confirmed, a supervisor will call",
    Status.REFUNDED: "refunded",
    Status.FAILED: "could not be completed",
    Status.REJECTED: "plastic not accepted",
}


@dataclass
class Session:
    user_id: UUID
    role: str
    channel: str | None
    screen: str
    context: dict[str, Any]


@dataclass(frozen=True)
class Step:
    text: str
    end: bool
    screen: str = "done"
    context: dict[str, Any] = field(default_factory=dict)


def show(text: str, screen: str, **context: Any) -> Step:
    return Step(_fit(text), False, screen, context)


def finish(text: str) -> Step:
    return Step(_fit(text), True)


async def handle(conn: Conn, request: UssdRequest, phone_e164: str) -> UssdReply:
    cur = await conn.execute(
        """
        select msisdn, screen, context, last_step, last_reply, last_end,
               updated_at < now() - %s::interval as idle
          from engine.ussd_sessions
         where provider = %s and session_id = %s
           for update
        """,
        (SESSION_IDLE, request.provider, request.session_id),
    )
    row = await cur.fetchone()
    live = row is not None and not row["idle"]
    if live and row["msisdn"] != phone_e164:
        await events.record(
            conn,
            "ussd.wrong_number",
            actor_type="provider",
            payload={"provider": request.provider},
        )
        return UssdReply(WRONG_NUMBER, end=True)
    # A provider resending a step we already answered gets the same answer.
    if (
        live
        and request.step is not None
        and row["last_step"] is not None
        and request.step <= row["last_step"]
        and row["last_reply"] is not None
    ):
        return UssdReply(row["last_reply"], bool(row["last_end"]))

    user = await _user_by_phone(conn, phone_e164)
    if user is None:
        return UssdReply(NOT_REGISTERED, end=True)
    if user["role"] not in ("rider", "household", "shop"):
        return UssdReply("Please use the Imfucuza supervisor app.", end=True)

    session = Session(user["id"], user["role"], user["channel"], "home", {})
    if live and not request.is_new:
        session.screen, session.context = row["screen"], row["context"]
        step = await _answer(conn, session, request.input.strip())
    else:
        step = await _home(conn, session)
        await conn.execute(
            "delete from engine.ussd_sessions where updated_at < now() - %s::interval",
            (SESSION_RETENTION,),
        )

    await conn.execute(
        """
        insert into engine.ussd_sessions
            (provider, session_id, msisdn, user_id, screen, context,
             last_step, last_reply, last_end)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (provider, session_id) do update
           set msisdn = excluded.msisdn, user_id = excluded.user_id,
               screen = excluded.screen, context = excluded.context,
               last_step = excluded.last_step, last_reply = excluded.last_reply,
               last_end = excluded.last_end, updated_at = now()
         -- Never let another number take over a live session.
         where engine.ussd_sessions.msisdn = excluded.msisdn
            or engine.ussd_sessions.updated_at < now() - %s::interval
        """,
        (
            request.provider,
            request.session_id,
            phone_e164,
            session.user_id,
            step.screen,
            Jsonb(step.context),
            request.step,
            step.text,
            step.end,
            SESSION_IDLE,
        ),
    )
    return UssdReply(step.text, step.end)


async def _answer(conn: Conn, session: Session, choice: str) -> Step:
    handler = _SCREENS.get(session.screen)
    if handler is None:
        return await _home(conn, session)
    try:
        # A failed action rolls back on its own and ends the session politely.
        async with conn.transaction():
            return await handler(conn, session, choice)
    except (InvalidTransition, StaleVersion):
        return finish("That step isn't possible for this pickup right now.")
    except (dispatch.OfferNotAvailable, dispatch.OfferNotFound):
        return finish("That job offer is no longer available.")
    except dispatch.TooManyLocationChanges:
        return finish("You have changed your location too often. Try again in an hour.")
    except plastic.WeightOutOfRange as exc:
        return finish(f"{str(exc).capitalize()}. Choose 5 to try again.")
    except pickup_flow.TooSoon as exc:
        minutes = exc.seconds_left // 60 + 1
        return finish(f"Arrived already? The trip takes longer. Try again in {minutes} min.")
    except pins.PinLocked:
        return finish("This PIN is locked after too many tries. A supervisor will call.")
    except pins.TooManyAttempts:
        return finish("Too many wrong PINs. Please try again in an hour.")
    except pins.PinUnavailable:
        return finish("There is no PIN waiting for this pickup.")
    except pickup_flow.NotYourPickup:
        return finish("That pickup is no longer yours.")
    except Exception:
        # The savepoint has rolled back. The provider needs a screen, not a 500.
        log.exception("ussd screen %s failed", session.screen)
        return finish(SORRY)


# Home ------------------------------------------------------------------------


async def _home(conn: Conn, session: Session) -> Step:
    if session.role == "household":
        return show(HOUSEHOLD_HOME, "household_home")
    if session.role == "shop":
        return show(SHOP_HOME, "shop_home")
    rider = await _rider(conn, session.user_id)
    return show(
        RIDER_HOME.format(
            duty="on duty" if rider["on_duty"] else "off duty",
            toggle="Stop shift" if rider["on_duty"] else "Start shift",
        ),
        "rider_home",
    )


async def _rider_home(conn: Conn, session: Session, choice: str) -> Step:
    rider = await _rider(conn, session.user_id)
    gps_only = "Use the app for this: it uses your phone's GPS."

    if choice == "1":
        await dispatch.set_duty(
            conn,
            rider["id"],
            on_duty=not rider["on_duty"],
            actor_type="rider",
            user_id=session.user_id,
        )
        if rider["on_duty"]:
            return finish("You are off duty. No jobs will be sent to you.")
        return finish("You are on duty. Choose 2 to set your location so jobs can reach you.")
    if choice == "2":
        if session.channel != "ussd":
            return finish(gps_only)
        return await _zones(conn, page=0)
    if choice == OFFER:
        return await _offer(conn, rider)
    if choice == "4":
        if session.channel != "ussd":
            return finish(gps_only)
        job = await _job_in(conn, rider["id"], Status.ASSIGNED)
        if job is None:
            return finish("You have no job to arrive at.")
        await pickup_flow.arrive_self_reported(
            conn, job["id"], rider_id=rider["id"], user_id=session.user_id
        )
        return finish("Marked as arrived. Collect the waste, then choose 5.")
    if choice == "5":
        job = await _job_in(conn, rider["id"], Status.ARRIVED) or await _job_in(
            conn, rider["id"], Status.RIDER_REVIEW
        )
        if job is None:
            if await _job_in(conn, rider["id"], Status.VERIFYING):
                return finish("The photo is being checked. You will get an SMS.")
            return finish("Choose 4 when you arrive, then 5 once you have collected.")
        if job["offering"] == "refuse":
            await pickup_flow.collected(
                conn, job["id"], rider_id=rider["id"], user_id=session.user_id
            )
            return finish("Collected. The household gets a PIN by SMS. Ask for it, then choose 6.")
        if session.channel != "ussd":
            return finish("Use the app for plastic: it sends the photo.")
        if job["status"] == Status.RIDER_REVIEW:
            return show(
                "Check the plastic in front of you. Does it qualify?\n1 Accept it\n2 Reject it",
                "rider_material",
                pickup_id=str(job["id"]),
            )
        prompt = (
            f"Saved: {plastic.kg(job['weight_g'])} kg. To change it, type the weight in kg:"
            if job["weight_g"]
            else "Weight of the plastic in kg, for example 4.2:"
        )
        return show(prompt, "rider_weight", pickup_id=str(job["id"]))
    if choice == "6":
        job = await _job_in(conn, rider["id"], Status.AWAITING_PIN)
        if job is None:
            return finish("No pickup is waiting for a PIN.")
        return show("Enter the household's 6-digit PIN:", "rider_pin", pickup_id=str(job["id"]))
    return show("Choose 1 to 6.\n" + (await _home(conn, session)).text, "rider_home")


# Rider: location ---------------------------------------------------------------


async def _zones(conn: Conn, page: int) -> Step:
    cur = await conn.execute("select id, name from engine.zones order by ussd_index, id")
    zones = await cur.fetchall()
    if not zones:
        return finish("No areas are set up yet. Ask a supervisor.")
    text, ids = _page("Your area:", zones, page, "0 Back")
    return show(text, "pick_zone", page=page, ids=ids)


async def _pick_zone(conn: Conn, session: Session, choice: str) -> Step:
    page, ids = session.context["page"], session.context["ids"]
    if choice == "0":
        return await _home(conn, session)
    if choice == "9":
        return await _zones(conn, page + 1)
    zone_id = _chosen(ids, choice)
    if zone_id is None:
        return await _zones(conn, page)
    return await _landmarks(conn, zone_id, page=0)


async def _landmarks(conn: Conn, zone_id: int, page: int) -> Step:
    cur = await conn.execute(
        "select id, name from engine.landmarks where zone_id = %s order by ussd_index, id",
        (zone_id,),
    )
    text, ids = _page("Nearest place:", await cur.fetchall(), page, "0 Area centre")
    return show(text, "pick_landmark", zone_id=zone_id, page=page, ids=ids)


async def _pick_landmark(conn: Conn, session: Session, choice: str) -> Step:
    zone_id = session.context["zone_id"]
    page, ids = session.context["page"], session.context["ids"]
    if choice == "9":
        return await _landmarks(conn, zone_id, page + 1)
    if choice == "0":
        cur = await conn.execute(
            "select name, lat, lng from engine.zones where id = %s", (zone_id,)
        )
        place, source = await cur.fetchone(), "zone"
    else:
        landmark_id = _chosen(ids, choice)
        if landmark_id is None:
            return await _landmarks(conn, zone_id, page)
        cur = await conn.execute(
            "select name, lat, lng from engine.landmarks where id = %s", (landmark_id,)
        )
        place, source = await cur.fetchone(), "landmark"
    rider = await _rider(conn, session.user_id)
    await dispatch.record_self_report(
        conn, rider["id"], source=source, lat=place["lat"], lng=place["lng"]
    )
    return finish(f"Location saved: {place['name']}. Update it when you move.")


# Rider: offers and PIN -------------------------------------------------------------


async def _offer(conn: Conn, rider: dict) -> Step:
    cur = await conn.execute(
        """
        select o.id, o.distance_m, p.offering, p.fee_pesewas,
               ceil(extract(epoch from o.expires_at - now()) / 60)::int as minutes_left
          from engine.dispatch_offers o
          join engine.pickup_requests p on p.id = o.pickup_id
         where o.rider_id = %s and o.response is null and o.expires_at > now()
        """,
        (rider["id"],),
    )
    offer = await cur.fetchone()
    if offer is None:
        return finish("No job offer right now.")
    settings = get_settings()
    if offer["offering"] == "plastic":
        pay = f"GHS {settings.rider_pay_pesewas_per_kg / 100:.2f} per kg"
    else:
        pay = f"GHS {offer['fee_pesewas'] * settings.rider_share_percent // 100 / 100:.2f}"
    text = (
        f"Job: {offer['offering']} pickup {offer['distance_m'] / 1000:.1f} km away, "
        f"you earn {pay}. {offer['minutes_left']} min left.\n1 Accept\n2 Decline"
    )
    return show(text, "offer", offer_id=str(offer["id"]))


async def _answer_offer(conn: Conn, session: Session, choice: str) -> Step:
    rider = await _rider(conn, session.user_id)
    offer_id = UUID(session.context["offer_id"])
    if choice == "1":
        result = await dispatch.accept(
            conn, offer_id, rider_id=rider["id"], user_id=session.user_id
        )
        await _offer_response(conn, session, offer_id, result.pickup_id, "accepted")
        await jobs.enqueue(
            conn,
            "sms.job_details",
            {"pickup_id": str(result.pickup_id), "rider_user_id": str(session.user_id)},
        )
        return finish("Accepted. Job details are coming by SMS. Choose 4 when you arrive.")
    if choice == "2":
        cur = await conn.execute(
            "select pickup_id from engine.dispatch_offers where id = %s", (offer_id,)
        )
        offer = await cur.fetchone()
        await dispatch.decline(conn, offer_id, rider_id=rider["id"], user_id=session.user_id)
        await _offer_response(conn, session, offer_id, offer and offer["pickup_id"], "declined")
        return finish("Declined.")
    return show("Choose 1 to accept or 2 to decline.", "offer", offer_id=str(offer_id))


async def _rider_weight(conn: Conn, session: Session, choice: str) -> Step:
    pickup_id = session.context["pickup_id"]
    grams = _grams(choice)
    if grams is None:
        return show("Type the weight in kg, for example 4.2:", "rider_weight", pickup_id=pickup_id)
    # The exact figure, since a stray key can't be seen once it's typed.
    return show(
        f"Save {plastic.kg(grams)} kg?\n1 Yes\n2 Change",
        "rider_weight_confirm",
        pickup_id=pickup_id,
        grams=grams,
    )


async def _rider_weight_confirm(conn: Conn, session: Session, choice: str) -> Step:
    pickup_id, grams = session.context["pickup_id"], session.context["grams"]
    if choice == "2":
        return show("Type the weight in kg, for example 4.2:", "rider_weight", pickup_id=pickup_id)
    if choice != "1":
        return show(
            f"Save {plastic.kg(grams)} kg?\n1 Yes\n2 Change",
            "rider_weight_confirm",
            pickup_id=pickup_id,
            grams=grams,
        )
    rider = await _rider(conn, session.user_id)
    await plastic.enter_weight(
        conn, UUID(pickup_id), rider_id=rider["id"], user_id=session.user_id, weight_g=grams
    )
    return finish(
        f"Saved: {plastic.kg(grams)} kg. The household now takes a photo in the app. "
        "You will get an SMS once it is checked."
    )


# Up to 999 kg with up to 3 decimals: nothing else reaches Decimal, so an
# exponent like 9e999999 can't make the conversion run for seconds.
_KG = re.compile(r"\d{1,3}(?:[.,]\d{1,3})?")


def _grams(text: str) -> int | None:
    """Kilograms as typed on a keypad, "4.2" or "4,2", in whole grams."""
    text = text.strip()
    if not _KG.fullmatch(text):
        return None
    grams = int(Decimal(text.replace(",", ".")) * 1000)
    return grams or None


async def _rider_material(conn: Conn, session: Session, choice: str) -> Step:
    pickup_id = session.context["pickup_id"]
    if choice not in ("1", "2"):
        return show(
            "Choose 1 to accept the plastic or 2 to reject it.",
            "rider_material",
            pickup_id=pickup_id,
        )
    rider = await _rider(conn, session.user_id)
    outcome = await plastic.rider_decides(
        conn, UUID(pickup_id), rider_id=rider["id"], user_id=session.user_id, accept=choice == "1"
    )
    if outcome == Status.REJECTED:
        return finish("Rejected. Leave the plastic with the household.")
    return finish("Thank you. Take the plastic, ask the household for the PIN, then choose 6.")


async def _rider_pin(conn: Conn, session: Session, choice: str) -> Step:
    pickup_id = UUID(session.context["pickup_id"])
    rider = await _rider(conn, session.user_id)
    await pickup_flow.assigned_to(conn, pickup_id, rider["id"])
    return await _confirm(conn, session, pickup_id, choice, via="rider_entry", actor_type="rider")


# Shop ------------------------------------------------------------------------


async def _shop_home(conn: Conn, session: Session, choice: str) -> Step:
    if choice == "1":
        return show("Type the 10-digit voucher code:", "shop_code")
    return show("Choose 1.\n" + SHOP_HOME, "shop_home")


async def _shop_code(conn: Conn, session: Session, choice: str) -> Step:
    code = re.sub(r"\s", "", choice)
    if not re.fullmatch(r"\d{10}", code):
        return show("A voucher code has 10 digits. Type it again:", "shop_code")
    try:
        found = await rewards.look_up(conn, session.user_id, code)
    except rewards.TooManyWrongCodes:
        return finish("Too many wrong codes. Please try again in an hour.")
    if found is None:
        return finish("No voucher has that code. Check it with the customer and dial again.")
    if found["status"] == "used" and found["used_here"]:
        return finish(
            f"You redeemed this voucher on {found['used_at']:%d %b}. "
            f"If you haven't yet, give the customer {found['name']}."
        )
    if found["status"] == "used":
        return finish(f"This voucher was already used on {found['used_at']:%d %b}.")
    if found["status"] == "expired" or found["lapsed"]:
        return finish(f"This voucher expired on {found['expires_at']:%d %b}.")
    return show(
        f"Valid: {found['name']}.\n1 Give it now\n0 Cancel",
        "shop_confirm",
        voucher_id=str(found["id"]),
    )


async def _shop_confirm(conn: Conn, session: Session, choice: str) -> Step:
    if choice != "1":
        return finish("Cancelled. The voucher can still be used.")
    try:
        used = await rewards.redeem(conn, UUID(session.context["voucher_id"]), session.user_id)
    except rewards.VoucherUnavailable:
        return finish("Sorry, this voucher has just been used or has expired.")
    return finish(f"Done. Give the customer {used['name']}. Ref {used['code'][-4:]}.")


# Household -------------------------------------------------------------------


async def _household_home(conn: Conn, session: Session, choice: str) -> Step:
    if choice == "1":
        waiting = await _waiting_for_pin(conn, session.user_id)
        if waiting is None:
            return finish("No pickup is waiting for your PIN.")
        return show(
            "Enter the 6-digit PIN from your SMS:", "household_pin", pickup_id=str(waiting["id"])
        )
    if choice == "2":
        latest = await _latest_pickup(conn, session.user_id)
        if latest is None or not latest["recent"]:
            return finish("You have no recent pickup to report.")
        return show(
            "Report a problem with your pickup? A supervisor will call you.\n1 Yes, report\n0 Back",
            "household_report",
            pickup_id=str(latest["id"]),
        )
    if choice == "3":
        latest = await _latest_pickup(conn, session.user_id)
        if latest is None:
            return finish("You have no pickups yet.")
        when = latest["requested_at"].strftime("%d %b")
        status = STATUS_WORDS.get(latest["status"], "in progress")
        return finish(f"Your pickup of {when}: {status}.")
    if choice == "4":
        household_id = await people.household_id_for(conn, session.user_id)
        held = await plastic.balance(conn, household_id)
        waiting = f" {held['pending']} more wait for a check." if held["pending"] else ""
        return finish(f"You have {held['available']} points.{waiting}")
    return show("Choose 1 to 4.\n" + HOUSEHOLD_HOME, "household_home")


async def _household_pin(conn: Conn, session: Session, choice: str) -> Step:
    pickup_id = UUID(session.context["pickup_id"])
    await _own_pickup(conn, session.user_id, pickup_id)
    return await _confirm(
        conn, session, pickup_id, choice, via="household_ussd", actor_type="household"
    )


async def _household_report(conn: Conn, session: Session, choice: str) -> Step:
    """A report only flags the pickup for a supervisor. It changes nothing, so it
    can't strand money: without a PIN the rider isn't paid, and an unconfirmed
    pickup is refunded after a day."""
    if choice != "1":
        return await _home(conn, session)
    pickup = await _own_pickup(conn, session.user_id, UUID(session.context["pickup_id"]))
    await review.open_item(
        conn,
        "household_complaint",
        pickup_id=pickup["id"],
        household_id=pickup["household_id"],
        payload={"reason": "reported by USSD"},
    )
    if pickup["status"] == Status.AWAITING_PIN:
        return finish("Reported. Don't give your PIN until it's sorted. A supervisor will call.")
    return finish("Reported. A supervisor will call you.")


# Shared ----------------------------------------------------------------------


async def _confirm(
    conn: Conn, session: Session, pickup_id: UUID, pin: str, *, via: str, actor_type: str
) -> Step:
    if not (len(pin) == pins.PIN_DIGITS and pin.isdigit()):
        return show(
            f"A PIN is {pins.PIN_DIGITS} digits. Enter it again:",
            session.screen,
            pickup_id=str(pickup_id),
        )
    result = await pins.confirm(
        conn, pickup_id, pin, via=via, actor_type=actor_type, actor_user_id=session.user_id
    )
    if result.confirmed:
        return finish("Confirmed. Thank you for using Imfucuza.")
    if result.locked:
        return finish("Wrong PIN. It is now locked, and a supervisor will call.")
    return finish(f"Wrong PIN. {result.tries_left} tries left. Dial again to retry.")


@jobs.handler("sms.job_details")
async def _job_details_sms(conn: Conn, payload: dict) -> None:
    """The USSD screen vanishes, so where to go and whom to call come by SMS.
    Built when sent, so the household's number never sits in a job payload,
    and stored masked."""
    pickup_id = UUID(payload["pickup_id"])
    cur = await conn.execute(
        """
        select p.status, h.address_text, u.phone_e164,
               (select l.name from engine.landmarks l
                 order by (l.lat - p.lat) ^ 2 + (l.lng - p.lng) ^ 2 limit 1) as landmark
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
          join engine.users u on u.id = h.user_id
         where p.id = %s
           and p.assigned_rider_id = (select id from engine.riders where user_id = %s)
        """,
        (pickup_id, payload["rider_user_id"]),
    )
    job = await cur.fetchone()
    # Only while the job is still theirs: a supervisor may have moved it.
    if job is None or job["status"] not in (Status.ASSIGNED, Status.ARRIVED):
        return
    near = f"near {job['landmark']}" if job["landmark"] else "at the pickup point"
    where = f"{job['address_text']}, {near}" if job["address_text"] else near
    phone = job["phone_e164"]
    text = f"Imfucuza job: {where}. Household: {phone}. Call if you can't find it."
    await notify.send_now(
        conn,
        user_id=UUID(payload["rider_user_id"]),
        pickup_id=pickup_id,
        template="job_details",
        text=text,
        masked=text.replace(phone, phone[:6] + "*" * (len(phone) - 6)),
    )


async def _offer_response(
    conn: Conn, session: Session, offer_id: UUID, pickup_id: UUID | None, response: str
) -> None:
    await events.record(
        conn,
        "ussd.offer_response",
        actor_type="rider",
        actor_id=session.user_id,
        pickup_id=pickup_id,
        payload={"offer_id": str(offer_id), "response": response},
    )


async def _user_by_phone(conn: Conn, phone: str) -> dict | None:
    cur = await conn.execute(
        """
        select u.id, u.role, r.channel
          from engine.users u
          left join engine.riders r on r.user_id = u.id
         where u.phone_e164 = %s and u.status = 'active' and u.consent_at is not null
        """,
        (phone,),
    )
    return await cur.fetchone()


async def _rider(conn: Conn, user_id: UUID) -> dict:
    cur = await conn.execute("select id, on_duty from engine.riders where user_id = %s", (user_id,))
    return await cur.fetchone()


async def _job_in(conn: Conn, rider_id: UUID, status: Status) -> dict | None:
    cur = await conn.execute(
        """
        select id, offering, status, weight_g from engine.pickup_requests
         where assigned_rider_id = %s and status = %s
         order by assigned_at limit 1
        """,
        (rider_id, status),
    )
    return await cur.fetchone()


async def _waiting_for_pin(conn: Conn, user_id: UUID) -> dict | None:
    cur = await conn.execute(
        """
        select p.id from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
         where h.user_id = %s and p.status = 'awaiting_pin'
         order by p.collected_at limit 1
        """,
        (user_id,),
    )
    return await cur.fetchone()


async def _latest_pickup(conn: Conn, user_id: UUID) -> dict | None:
    cur = await conn.execute(
        """
        select p.id, p.status, p.requested_at, p.household_id,
               p.requested_at > now() - interval '7 days' as recent
          from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
         where h.user_id = %s
         order by p.requested_at desc limit 1
        """,
        (user_id,),
    )
    return await cur.fetchone()


async def _own_pickup(conn: Conn, user_id: UUID, pickup_id: UUID) -> dict:
    cur = await conn.execute(
        """
        select p.id, p.status, p.household_id from engine.pickup_requests p
          join engine.households h on h.id = p.household_id
         where p.id = %s and h.user_id = %s
        """,
        (pickup_id, user_id),
    )
    pickup = await cur.fetchone()
    if pickup is None:
        raise pickup_flow.NotYourPickup("pickup not found")
    return pickup


def _page(header: str, rows: list[dict], page: int, back: str) -> tuple[str, list]:
    """One page of a list, numbered 1 to PAGE_SIZE, with "9 More" when there is
    more. Returns the screen and the ids behind each number."""
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    page %= pages
    chunk = rows[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = [header] + [f"{i} {r['name'][:NAME_WIDTH]}" for i, r in enumerate(chunk, 1)]
    if pages > 1:
        lines.append("9 More")
    lines.append(back)
    return "\n".join(lines), [r["id"] for r in chunk]


def _chosen(ids: list, choice: str) -> Any:
    if choice.isdigit() and 1 <= int(choice) <= len(ids):
        return ids[int(choice) - 1]
    return None


def _fit(text: str) -> str:
    """A safety net: screens are built to fit, so cutting one is a bug worth logging."""
    if len(text) <= MAX_SCREEN:
        return text
    log.warning("ussd screen too long (%d characters), cut to fit", len(text))
    lines, kept = text.split("\n"), []
    for line in lines:
        if len("\n".join([*kept, line])) > MAX_SCREEN:
            break
        kept.append(line)
    return "\n".join(kept) if kept else text[:MAX_SCREEN]


Handler = Callable[[Conn, Session, str], Awaitable[Step]]

_SCREENS: dict[str, Handler] = {
    "rider_home": _rider_home,
    "pick_zone": _pick_zone,
    "pick_landmark": _pick_landmark,
    "offer": _answer_offer,
    "rider_pin": _rider_pin,
    "shop_home": _shop_home,
    "shop_code": _shop_code,
    "shop_confirm": _shop_confirm,
    "rider_weight": _rider_weight,
    "rider_weight_confirm": _rider_weight_confirm,
    "rider_material": _rider_material,
    "household_home": _household_home,
    "household_pin": _household_pin,
    "household_report": _household_report,
}
