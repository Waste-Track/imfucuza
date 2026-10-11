"""Rewards (engine-design.md section 4). Households spend points on vouchers
that partner shops redeem by USSD, and households that opt in appear on a
monthly leaderboard for their zone."""

import re
import secrets
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg import errors

from app import jobs
from app.config import get_settings
from app.db import Conn
from app.domain import events, ledger, money, notify

CODE_DIGITS = 10
# Wrong codes a shop may type, so codes can't be guessed (threat model T13).
SHOP_WRONG_CODES_PER_HOUR = 10
SHOP_WRONG_CODES_PER_DAY = 30
# Unused vouchers a household can hold. Caps what a hijacked phone can drain at
# once (threat model T3) and how many live codes there are to guess.
MAX_ACTIVE_VOUCHERS = 3
LEADERBOARD_SIZE = 10
# Names that would pass as the service or its staff.
_RESERVED_NAMES = {"imfucuza", "supervisor", "admin", "rider", "support"}
# What counts towards a rank: points earned. Spending them doesn't lower it.
_EARNING_KINDS = ["points_accrue", "points_release"]


class NotEnoughPoints(Exception):
    pass


class ProductUnavailable(Exception):
    pass


class VoucherUnavailable(Exception):
    pass


class TooManyWrongCodes(Exception):
    pass


class TooManyVouchers(Exception):
    pass


class NameTaken(Exception):
    pass


# The catalogue -------------------------------------------------------------------


async def catalogue(conn: Conn, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select id, name, points_cost, face_value_pesewas, active
          from engine.voucher_products
         where active or %s
         order by points_cost, name
        """,
        (include_inactive,),
    )
    return await cur.fetchall()


async def add_product(
    conn: Conn, *, name: str, points_cost: int, face_value_pesewas: int, supervisor_id: UUID
) -> UUID:
    cur = await conn.execute(
        """
        insert into engine.voucher_products (name, points_cost, face_value_pesewas, created_by)
        values (%s, %s, %s, %s)
        returning id
        """,
        (name, points_cost, face_value_pesewas, supervisor_id),
    )
    product_id = (await cur.fetchone())["id"]
    await events.record(
        conn,
        "rewards.product_added",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"product_id": str(product_id), "points_cost": points_cost},
    )
    return product_id


async def set_product_active(
    conn: Conn, product_id: UUID, *, active: bool, supervisor_id: UUID
) -> None:
    """Withdrawing a product stops new vouchers. Vouchers already issued still work."""
    cur = await conn.execute(
        "update engine.voucher_products set active = %s where id = %s returning id",
        (active, product_id),
    )
    if await cur.fetchone() is None:
        raise LookupError("product not found")
    await events.record(
        conn,
        "rewards.product_active",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"product_id": str(product_id), "active": active},
    )


# Vouchers --------------------------------------------------------------------------


async def issue(conn: Conn, household_id: UUID, product_id: UUID, user_id: UUID) -> dict:
    # Locked, so two purchases at once can't both get under the cap.
    await conn.execute(
        "select id from engine.households where id = %s for no key update", (household_id,)
    )
    cur = await conn.execute(
        "select count(*) as n from engine.vouchers where household_id = %s and status = 'active'",
        (household_id,),
    )
    if (await cur.fetchone())["n"] >= MAX_ACTIVE_VOUCHERS:
        raise TooManyVouchers(f"use one of your {MAX_ACTIVE_VOUCHERS} vouchers first")
    cur = await conn.execute(
        "select name, points_cost from engine.voucher_products where id = %s and active",
        (product_id,),
    )
    product = await cur.fetchone()
    if product is None:
        raise ProductUnavailable("that voucher isn't on offer")
    voucher_id = uuid4()
    valid = timedelta(days=get_settings().voucher_valid_days)
    try:
        # The voucher and its points go together or not at all.
        async with conn.transaction():
            for _ in range(3):
                cur = await conn.execute(
                    """
                    insert into engine.vouchers
                        (id, household_id, product_id, code, points, expires_at)
                    values (%s, %s, %s, %s, %s, now() + %s)
                    on conflict (code) do nothing
                    returning expires_at
                    """,
                    (
                        voucher_id,
                        household_id,
                        product_id,
                        _new_code(),
                        product["points_cost"],
                        valid,
                    ),
                )
                row = await cur.fetchone()
                if row:
                    break
            else:
                raise RuntimeError("couldn't find an unused voucher code")
            await ledger.post(
                conn, money.voucher_redeem(voucher_id, household_id, product["points_cost"])
            )
    except ledger.InsufficientBalance as exc:
        raise NotEnoughPoints(f"{product['name']} costs {product['points_cost']} points") from exc
    await jobs.enqueue(
        conn,
        "voucher.expire",
        {"voucher_id": str(voucher_id)},
        run_at=row["expires_at"],
        dedupe_key=f"voucher-expire:{voucher_id}",
    )
    await jobs.enqueue(conn, "voucher.sms", {"voucher_id": str(voucher_id)})
    await events.record(
        conn,
        "voucher.issued",
        actor_type="household",
        actor_id=user_id,
        payload={"voucher_id": str(voucher_id), "points": product["points_cost"]},
    )
    return await voucher(conn, voucher_id, household_id)


async def voucher(conn: Conn, voucher_id: UUID, household_id: UUID) -> dict:
    cur = await conn.execute(
        _VOUCHERS + " where v.id = %s and v.household_id = %s", (voucher_id, household_id)
    )
    return await cur.fetchone()


async def household_vouchers(conn: Conn, household_id: UUID) -> list[dict]:
    cur = await conn.execute(
        _VOUCHERS + " where v.household_id = %s order by v.issued_at desc limit 50",
        (household_id,),
    )
    return await cur.fetchall()


_VOUCHERS = """
    select v.id, p.name as product, v.points, v.status, v.issued_at, v.expires_at, v.used_at,
           s.name as used_at_shop,
           case when v.status = 'active' then v.code end as code
      from engine.vouchers v
      join engine.voucher_products p on p.id = v.product_id
      left join engine.partner_shops s on s.id = v.used_at_shop_id
"""


def spaced(code: str) -> str:
    return f"{code[:4]} {code[4:8]} {code[8:]}"


def _new_code() -> str:
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


@jobs.handler("voucher.sms")
async def _voucher_sms(conn: Conn, payload: dict[str, Any]) -> None:
    """Built when sent, so the code never sits in a job payload, and stored masked."""
    cur = await conn.execute(
        """
        select v.code, v.status, v.expires_at, p.name, h.user_id
          from engine.vouchers v
          join engine.voucher_products p on p.id = v.product_id
          join engine.households h on h.id = v.household_id
         where v.id = %s
        """,
        (payload["voucher_id"],),
    )
    found = await cur.fetchone()
    if found is None or found["status"] != "active":
        return
    code = spaced(found["code"])
    text = (
        f"Imfucuza: your voucher for {found['name']} is {code}. Show it at a partner shop "
        f"by {found['expires_at']:%d %b}. Anyone with the code can use it."
    )
    await notify.send_now(
        conn,
        user_id=found["user_id"],
        template="voucher",
        text=text,
        masked=text.replace(code, "**** **** " + code[-2:]),
    )


@jobs.handler("voucher.expire")
async def _expire(conn: Conn, payload: dict[str, Any]) -> None:
    """Unused by its date: the points go back to the household."""
    cur = await conn.execute(
        """
        update engine.vouchers set status = 'expired'
         where id = %s and status = 'active' and expires_at <= now()
        returning household_id, points,
                  (select name from engine.voucher_products p where p.id = product_id) as name
        """,
        (payload["voucher_id"],),
    )
    expired = await cur.fetchone()
    if expired is None:
        return
    voucher_id = UUID(payload["voucher_id"])
    await ledger.post(
        conn, money.voucher_void(voucher_id, expired["household_id"], expired["points"])
    )
    cur = await conn.execute(
        "select user_id from engine.households where id = %s", (expired["household_id"],)
    )
    await notify.queue_sms(
        conn,
        user_id=(await cur.fetchone())["user_id"],
        template="voucher_expired",
        text=f"Imfucuza: your voucher for {expired['name']} expired unused. "
        f"Its {expired['points']} points are back in your balance.",
    )


# Shops --------------------------------------------------------------------------------


async def look_up(conn: Conn, shop_user_id: UUID, code: str) -> dict | None:
    """The voucher behind a code a shop typed, whatever its state. A code that
    matches nothing counts against the shop's allowance."""
    cur = await conn.execute(
        """
        select count(*) filter (where occurred_at > now() - interval '1 hour') as hour,
               count(*) as day
          from engine.events
         where name = 'voucher.wrong_code' and actor_id = %s
           and occurred_at > now() - interval '1 day'
        """,
        (shop_user_id,),
    )
    wrong = await cur.fetchone()
    if wrong["hour"] >= SHOP_WRONG_CODES_PER_HOUR or wrong["day"] >= SHOP_WRONG_CODES_PER_DAY:
        raise TooManyWrongCodes("too many wrong codes")
    cur = await conn.execute(
        """
        select v.id, v.status, v.points, v.expires_at, v.used_at, v.expires_at <= now() as lapsed,
               p.name, s.user_id = %s as used_here
          from engine.vouchers v
          join engine.voucher_products p on p.id = v.product_id
          left join engine.partner_shops s on s.id = v.used_at_shop_id
         where v.code = %s
        """,
        (shop_user_id, code),
    )
    found = await cur.fetchone()
    if found is None:
        await events.record(conn, "voucher.wrong_code", actor_type="shop", actor_id=shop_user_id)
    return found


async def redeem(conn: Conn, voucher_id: UUID, shop_user_id: UUID) -> dict:
    cur = await conn.execute(
        "select id, name from engine.partner_shops where user_id = %s", (shop_user_id,)
    )
    shop = await cur.fetchone()
    if shop is None:
        raise VoucherUnavailable("this phone isn't registered to a shop")
    cur = await conn.execute(
        """
        update engine.vouchers set status = 'used', used_at = now(), used_at_shop_id = %s
         where id = %s and status = 'active' and expires_at > now()
        returning household_id, code,
                  (select name from engine.voucher_products p where p.id = product_id) as name
        """,
        (shop["id"], voucher_id),
    )
    used = await cur.fetchone()
    if used is None:
        raise VoucherUnavailable("this voucher has just been used or has expired")
    await events.record(
        conn,
        "voucher.used",
        actor_type="shop",
        actor_id=shop_user_id,
        payload={"voucher_id": str(voucher_id), "shop_id": str(shop["id"])},
    )
    cur = await conn.execute(
        "select user_id from engine.households where id = %s", (used["household_id"],)
    )
    await notify.queue_sms(
        conn,
        user_id=(await cur.fetchone())["user_id"],
        template="voucher_used",
        text=f"Imfucuza: your voucher for {used['name']} was used at {shop['name']}. "
        "Not you? Tell a supervisor.",
    )
    return used


async def shop_report(
    conn: Conn, *, shop_id: UUID | None, start: date, end: date
) -> list[dict[str, Any]]:
    """What each shop redeemed, to settle with it."""
    cur = await conn.execute(
        """
        select s.id as shop_id, s.name as shop, count(v.id) as vouchers,
               coalesce(sum(p.face_value_pesewas), 0)::bigint as face_value_pesewas
          from engine.partner_shops s
          left join engine.vouchers v
            on v.used_at_shop_id = s.id and v.used_at >= %(start)s and v.used_at < %(end)s
          left join engine.voucher_products p on p.id = v.product_id
         where %(shop)s::uuid is null or s.id = %(shop)s
         group by s.id, s.name
         order by s.name
        """,
        {"start": start, "end": end, "shop": shop_id},
    )
    return await cur.fetchall()


async def set_shop_active(conn: Conn, shop_id: UUID, *, active: bool, supervisor_id: UUID) -> None:
    """A suspended shop's phone gets nowhere on USSD."""
    cur = await conn.execute(
        """
        update engine.users set status = %s
         where id = (select user_id from engine.partner_shops where id = %s)
           and status in ('active', 'suspended')
        returning id
        """,
        ("active" if active else "suspended", shop_id),
    )
    if await cur.fetchone() is None:
        raise LookupError("shop not found")
    await events.record(
        conn,
        "shop.status",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"shop_id": str(shop_id), "active": active},
    )


async def vouchers_for_phone(conn: Conn, phone_e164: str) -> list[dict]:
    """A household's vouchers, for a supervisor following up a report of misuse.
    Codes are never shown."""
    cur = await conn.execute(
        """
        select h.id from engine.households h join engine.users u on u.id = h.user_id
         where u.phone_e164 = %s
        """,
        (phone_e164,),
    )
    household = await cur.fetchone()
    if household is None:
        raise LookupError("household not found")
    return [{**v, "code": None} for v in await household_vouchers(conn, household["id"])]


# The leaderboard ------------------------------------------------------------------------


def clean_name(name: str) -> str:
    """Letters, spaces, apostrophes and hyphens only, so no phone number or
    other contact detail can be put on a board."""
    name = " ".join(name.split())
    if not re.fullmatch(r"[A-Za-z][A-Za-z '\-]{1,19}", name):
        raise ValueError("use 2 to 20 letters, with spaces, apostrophes or hyphens")
    if name.lower() in _RESERVED_NAMES:
        raise ValueError("choose another name")
    return name


async def set_leaderboard_name(
    conn: Conn,
    household_id: UUID,
    name: str | None,
    *,
    user_id: UUID,
    consent_version: str | None,
    channel: str,
) -> None:
    """Joining needs the leaderboard notice. Leaving clears the name and the
    consent on the household. The event log keeps what was accepted and when."""
    try:
        async with conn.transaction():
            await conn.execute(
                """
                update engine.households
                   set leaderboard_name = %(name)s,
                       leaderboard_consent_version = case when %(name)s::text is null then null
                                                          else %(version)s end,
                       leaderboard_consent_at = case when %(name)s::text is null then null
                                                     else now() end,
                       leaderboard_consent_channel = case when %(name)s::text is null then null
                                                          else %(channel)s end
                 where id = %(id)s
                """,
                {"name": name, "version": consent_version, "channel": channel, "id": household_id},
            )
    except errors.UniqueViolation as exc:
        raise NameTaken("another household has that name") from exc
    await events.record(
        conn,
        "household.leaderboard",
        actor_type="household",
        actor_id=user_id,
        payload={
            "household_id": str(household_id),
            "joined": name is not None,
            "consent_version": consent_version if name else None,
            "channel": channel,
        },
    )


async def clear_leaderboard_name(conn: Conn, household_id: UUID, *, supervisor_id: UUID) -> None:
    """A supervisor takes an offensive name off the board."""
    cur = await conn.execute(
        """
        update engine.households
           set leaderboard_name = null, leaderboard_consent_version = null,
               leaderboard_consent_at = null, leaderboard_consent_channel = null
         where id = %s
        returning id
        """,
        (household_id,),
    )
    if await cur.fetchone() is None:
        raise LookupError("household not found")
    await events.record(
        conn,
        "household.leaderboard_cleared",
        actor_type="supervisor",
        actor_id=supervisor_id,
        payload={"household_id": str(household_id)},
    )


async def leaderboard(conn: Conn, household_id: UUID, *, month: date) -> dict[str, Any] | None:
    """Only households that opted in are ranked and named. Anyone else sees
    where they would place, so the board never hints at who chose to stay off."""
    cur = await conn.execute(
        """
        select z.id, z.name from engine.zones z
         where z.id = (select engine.household_zone(h) from engine.households h
                        where h.id = %s)
        """,
        (household_id,),
    )
    zone = await cur.fetchone()
    if zone is None:
        return None
    start = month.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    cur = await conn.execute(
        """
        with earned as (
            select split_part(a.code, ':', 2)::uuid as household_id,
                   sum(e.amount)::bigint as points
              from engine.ledger_entries e
              join engine.ledger_accounts a on a.id = e.account_id
              join engine.ledger_transactions t on t.id = e.txn_id
             where a.code like 'points_available:%%' and e.side = 'credit'
               and t.kind = any(%(kinds)s)
               and t.created_at >= %(start)s and t.created_at < %(end)s
             group by 1
        )
        select h.id, h.leaderboard_name, e.points
          from engine.households h
          join engine.users u on u.id = h.user_id
          join earned e on e.household_id = h.id
         where engine.household_zone(h) = %(zone)s
           and (h.id = %(me)s or (h.leaderboard_name is not null and u.status = 'active'))
         order by e.points desc
        """,
        {
            "kinds": _EARNING_KINDS,
            "start": start,
            "end": end,
            "zone": zone["id"],
            "me": household_id,
        },
    )
    rows = await cur.fetchall()
    named = [r for r in rows if r["leaderboard_name"]]
    ranked = [{"rank": 1 + sum(o["points"] > r["points"] for o in named), **r} for r in named]
    me = next((r for r in rows if r["id"] == household_id), None)
    you = None
    if me:
        on_board = bool(me["leaderboard_name"])
        rank = 1 + sum(o["points"] > me["points"] for o in named if o["id"] != household_id)
        you = {"rank": rank, "points": me["points"], "on_board": on_board}
    return {
        "zone": dict(zone),
        "month": start.strftime("%Y-%m"),
        "top": [
            {"rank": r["rank"], "name": r["leaderboard_name"], "points": r["points"]}
            for r in ranked[:LEADERBOARD_SIZE]
        ],
        "you": you,
    }
