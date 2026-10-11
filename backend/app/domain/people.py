"""Users, households and riders."""

import re
from dataclasses import dataclass
from uuid import UUID

from app.db import Conn
from app.domain import events

_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def require_ghana(phone_e164: str) -> str:
    """Payments and SMS only reach Ghanaian numbers for now."""
    if not phone_e164.startswith("+233"):
        raise ValueError("only Ghanaian (+233) phone numbers can register for now")
    return phone_e164


def normalize_msisdn(raw: str) -> str:
    """Ghanaian and international numbers to E.164: '024 123 4567',
    '233241234567' and '+233241234567' all become '+233241234567'."""
    digits = re.sub(r"[\s\-()]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = "+233" + digits[1:]
    elif not digits.startswith("+"):
        digits = "+" + digits
    if not _E164.match(digits):
        raise ValueError("not a valid phone number")
    return digits


@dataclass(frozen=True)
class User:
    id: UUID
    role: str
    phone_e164: str
    name: str | None
    status: str
    consented: bool


def _user(row: dict) -> User:
    return User(
        id=row["id"],
        role=row["role"],
        phone_e164=row["phone_e164"],
        name=row["name"],
        status=row["status"],
        consented=row["consent_at"] is not None,
    )


async def find_signed_in_user(
    conn: Conn, auth_user_id: UUID, verified_phone: str | None
) -> User | None:
    """The Engine user behind a Supabase sign-in. On a first sign-in, link the
    account to a user registered earlier with the same verified phone (riders
    are registered by a supervisor before they ever open the app)."""
    cur = await conn.execute("select * from engine.users where auth_user_id = %s", (auth_user_id,))
    row = await cur.fetchone()
    if row or not verified_phone:
        return _user(row) if row else None

    cur = await conn.execute(
        """
        update engine.users set auth_user_id = %s
         where phone_e164 = %s and auth_user_id is null
        returning *
        """,
        (auth_user_id, normalize_msisdn(verified_phone)),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    await events.record(conn, "user.linked", actor_type=row["role"], actor_id=row["id"])
    return _user(row)


async def register_household(
    conn: Conn,
    *,
    auth_user_id: UUID,
    phone_e164: str,
    name: str | None,
    consent_version: str,
    lat: float | None,
    lng: float | None,
    address_text: str | None,
) -> tuple[User, UUID]:
    """Create or update the signed-in household. Consent is recorded every time."""
    cur = await conn.execute(
        """
        insert into engine.users
            (auth_user_id, role, phone_e164, name, consent_version, consent_at, consent_channel)
        values (%s, 'household', %s, %s, %s, now(), 'pwa')
        on conflict (auth_user_id) do update
           set name = coalesce(excluded.name, engine.users.name),
               consent_version = excluded.consent_version,
               consent_at = now(),
               consent_channel = 'pwa'
        returning *
        """,
        (auth_user_id, phone_e164, name, consent_version),
    )
    user = _user(await cur.fetchone())
    if user.role != "household":
        raise PermissionError(f"this account is registered as a {user.role}")

    cur = await conn.execute(
        """
        insert into engine.households (user_id, lat, lng, address_text)
        values (%s, %s, %s, %s)
        on conflict (user_id) do update
           set lat = coalesce(excluded.lat, engine.households.lat),
               lng = coalesce(excluded.lng, engine.households.lng),
               address_text = coalesce(excluded.address_text, engine.households.address_text)
        returning id
        """,
        (user.id, lat, lng, address_text),
    )
    household_id = (await cur.fetchone())["id"]
    await events.record(
        conn,
        "household.registered",
        actor_type="household",
        actor_id=user.id,
        payload={"consent_version": consent_version},
    )
    return user, household_id


async def register_rider(
    conn: Conn,
    *,
    phone_e164: str,
    name: str,
    channel: str,
    consent_version: str,
    registered_by: UUID,
) -> UUID:
    """A supervisor registers a rider, with consent taken in person."""
    cur = await conn.execute(
        """
        insert into engine.users
            (role, phone_e164, name, consent_version, consent_at, consent_channel)
        values ('rider', %s, %s, %s, now(), 'paper')
        returning id
        """,
        (phone_e164, name, consent_version),
    )
    user_id = (await cur.fetchone())["id"]
    cur = await conn.execute(
        """
        insert into engine.riders (user_id, channel, payout_msisdn) values (%s, %s, %s)
        returning id
        """,
        (user_id, channel, phone_e164),
    )
    rider_id = (await cur.fetchone())["id"]
    await events.record(
        conn,
        "rider.registered",
        actor_type="supervisor",
        actor_id=registered_by,
        payload={"rider_id": str(rider_id), "channel": channel},
    )
    return rider_id


async def household_id_for(conn: Conn, user_id: UUID) -> UUID | None:
    cur = await conn.execute("select id from engine.households where user_id = %s", (user_id,))
    row = await cur.fetchone()
    return row["id"] if row else None


async def rider_id_for(conn: Conn, user_id: UUID) -> UUID | None:
    cur = await conn.execute("select id from engine.riders where user_id = %s", (user_id,))
    row = await cur.fetchone()
    return row["id"] if row else None
