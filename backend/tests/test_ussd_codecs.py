import json
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest

from app.adapters.ussd_base import InvalidUssdRequest, UssdReply, UssdRequest
from app.adapters.ussd_codecs import CODECS, AfricasTalkingCodec, ArkeselCodec, MnotifyCodec

FORM = "application/x-www-form-urlencoded"
JSON = "application/json"
NOW = datetime(2026, 10, 10, 9, 30, tzinfo=UTC)
MISSING = object()

africastalking = AfricasTalkingCodec()
arkesel = ArkeselCodec()
mnotify = MnotifyCodec(now=lambda: NOW)

AT_REQUEST = {
    "sessionId": "ATUid_1a2b3c",
    "serviceCode": "*384*1234#",
    "phoneNumber": "+233241234567",
    "networkCode": "62001",
    "text": "",
}
ARKESEL_REQUEST = {
    "sessionID": "2005506191900168",
    "userID": "USSD_DOCUMENTATION",
    "newSession": True,
    "msisdn": "233271231234",
    "userData": "*928*1#",
    "network": "AIRTELTIGO",
}
MNOTIFY_REQUEST = {
    "msisdn": "233541509394",
    "sequenceID": "6789940010",
    "data": "*899*86#",
    "timestamp": "2025-09-01T11:22:33Z",
}


def fields(defaults: dict, changes: dict) -> dict:
    """The defaults with changes applied. A field set to MISSING is left out."""
    return {k: v for k, v in (defaults | changes).items() if v is not MISSING}


def at_body(**changes: object) -> bytes:
    return urlencode(fields(AT_REQUEST, changes)).encode()


def arkesel_body(**changes: object) -> bytes:
    return json.dumps(fields(ARKESEL_REQUEST, changes)).encode()


def mnotify_body(**changes: object) -> bytes:
    return json.dumps(fields(MNOTIFY_REQUEST, changes)).encode()


# Africa's Talking ------------------------------------------------------------


def test_africastalking_first_request_is_new_with_no_input():
    assert africastalking.parse(at_body(), FORM) == UssdRequest(
        "africastalking", "ATUid_1a2b3c", "+233241234567", "", is_new=True, step=0
    )


def test_africastalking_later_request_carries_only_the_latest_input():
    assert africastalking.parse(at_body(text="1*2*3"), FORM) == UssdRequest(
        "africastalking", "ATUid_1a2b3c", "+233241234567", "3", is_new=False, step=3
    )


def test_africastalking_empty_answer_after_earlier_ones_is_empty_input():
    request = africastalking.parse(at_body(text="1*"), FORM)

    assert (request.input, request.is_new) == ("", False)


def test_africastalking_continue_reply_starts_with_con():
    request = africastalking.parse(at_body(text="1"), FORM)

    reply = africastalking.render(request, UssdReply("Pick one\n1. Pay", end=False))

    assert reply == (b"CON Pick one\n1. Pay", "text/plain")


def test_africastalking_end_reply_starts_with_end():
    request = africastalking.parse(at_body(text="1*1"), FORM)

    reply = africastalking.render(request, UssdReply("Thanks, paid.", end=True))

    assert reply == (b"END Thanks, paid.", "text/plain")


@pytest.mark.parametrize("field", ["sessionId", "phoneNumber", "text"])
def test_africastalking_rejects_a_request_missing_a_required_field(field):
    with pytest.raises(InvalidUssdRequest):
        africastalking.parse(at_body(**{field: MISSING}), FORM)


@pytest.mark.parametrize("field", ["sessionId", "phoneNumber"])
@pytest.mark.parametrize("blank", ["", "  "])
def test_africastalking_rejects_a_blank_session_id_or_phone_number(field, blank):
    with pytest.raises(InvalidUssdRequest):
        africastalking.parse(at_body(**{field: blank}), FORM)


def test_africastalking_rejects_a_field_sent_twice():
    with pytest.raises(InvalidUssdRequest):
        africastalking.parse(at_body(text="1") + b"&text=2", FORM)


def test_africastalking_rejects_input_that_is_not_utf8():
    with pytest.raises(InvalidUssdRequest):
        africastalking.parse(at_body() + b"%FF", FORM)


# Arkesel ---------------------------------------------------------------------


def test_arkesel_first_request_is_new_with_no_input():
    request = arkesel.parse(arkesel_body(), JSON)

    assert (request.provider, request.msisdn) == ("arkesel", "233271231234")
    assert (request.input, request.is_new) == ("", True)


def test_arkesel_later_request_carries_the_users_answer():
    request = arkesel.parse(arkesel_body(newSession=False, userData="2"), JSON)

    assert (request.input, request.is_new) == ("2", False)


def test_arkesel_session_id_is_the_same_on_every_request_of_a_session():
    first = arkesel.parse(arkesel_body(), JSON)
    later = arkesel.parse(arkesel_body(newSession=False, userData="2"), JSON)

    assert first.session_id == later.session_id


def test_arkesel_continue_reply_echoes_the_request_ids():
    request = arkesel.parse(arkesel_body(newSession=False, userData="1"), JSON)

    reply = arkesel.render(request, UssdReply("Pick one\n1. Pay", end=False))

    assert reply == (
        b'{"sessionID":"2005506191900168","userID":"USSD_DOCUMENTATION",'
        b'"msisdn":"233271231234","message":"Pick one\\n1. Pay","continueSession":true}',
        "application/json",
    )


def test_arkesel_end_reply_stops_the_session():
    request = arkesel.parse(arkesel_body(newSession=False, userData="1"), JSON)

    reply = arkesel.render(request, UssdReply("Thanks, paid.", end=True))

    assert reply == (
        b'{"sessionID":"2005506191900168","userID":"USSD_DOCUMENTATION",'
        b'"msisdn":"233271231234","message":"Thanks, paid.","continueSession":false}',
        "application/json",
    )


def test_arkesel_reply_echoes_ids_exactly_whatever_they_contain():
    request = arkesel.parse(arkesel_body(sessionID="a:b%3A c", userID="app:1"), JSON)

    body, _ = arkesel.render(request, UssdReply("Hi", end=True))

    assert json.loads(body)["sessionID"] == "a:b%3A c"
    assert json.loads(body)["userID"] == "app:1"


@pytest.mark.parametrize("field", ["sessionID", "userID", "newSession", "msisdn", "userData"])
def test_arkesel_rejects_a_request_missing_a_required_field(field):
    with pytest.raises(InvalidUssdRequest):
        arkesel.parse(arkesel_body(**{field: MISSING}), JSON)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sessionID", 2005506191900168),
        ("sessionID", "\ud800"),
        ("userID", None),
        ("newSession", "true"),
        ("newSession", 1),
        ("msisdn", 233271231234),
        ("userData", 1),
    ],
)
def test_arkesel_rejects_a_field_of_the_wrong_type(field, value):
    with pytest.raises(InvalidUssdRequest):
        arkesel.parse(arkesel_body(**{field: value}), JSON)


@pytest.mark.parametrize("field", ["sessionID", "userID", "msisdn"])
def test_arkesel_rejects_a_blank_id(field):
    with pytest.raises(InvalidUssdRequest):
        arkesel.parse(arkesel_body(**{field: " "}), JSON)


# mNotify ---------------------------------------------------------------------


def test_mnotify_dialled_code_starts_a_new_session_with_no_input():
    request = mnotify.parse(mnotify_body(), JSON)

    assert (request.provider, request.msisdn) == ("mnotify", "233541509394")
    assert (request.input, request.is_new) == ("", True)


def test_mnotify_later_request_carries_the_users_answer():
    request = mnotify.parse(mnotify_body(data="2"), JSON)

    assert (request.input, request.is_new) == ("2", False)


def test_mnotify_later_input_is_passed_through_whole():
    assert mnotify.parse(mnotify_body(data="1*2"), JSON).input == "1*2"


def test_mnotify_sessions_with_the_same_sequence_id_stay_apart_per_phone():
    mine = mnotify.parse(mnotify_body(), JSON)
    theirs = mnotify.parse(mnotify_body(msisdn="233201234567"), JSON)

    assert mine.session_id != theirs.session_id


def test_mnotify_continue_reply_echoes_the_request_ids():
    request = mnotify.parse(mnotify_body(data="1"), JSON)

    reply = mnotify.render(request, UssdReply("Pick one\n1. Pay", end=False))

    assert reply == (
        b'{"msisdn":"233541509394","sequenceID":"6789940010","message":"Pick one\\r\\n1. Pay",'
        b'"timestamp":"2026-10-10T09:30:00Z","continueFlag":0}',
        "application/json",
    )


def test_mnotify_end_reply_sets_the_continue_flag_to_1():
    request = mnotify.parse(mnotify_body(data="1"), JSON)

    reply = mnotify.render(request, UssdReply("Thanks, paid.", end=True))

    assert reply == (
        b'{"msisdn":"233541509394","sequenceID":"6789940010","message":"Thanks, paid.",'
        b'"timestamp":"2026-10-10T09:30:00Z","continueFlag":1}',
        "application/json",
    )


@pytest.mark.parametrize("field", ["msisdn", "sequenceID", "data"])
def test_mnotify_rejects_a_request_missing_a_required_field(field):
    with pytest.raises(InvalidUssdRequest):
        mnotify.parse(mnotify_body(**{field: MISSING}), JSON)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("msisdn", 233541509394),
        ("sequenceID", 6789940010),
        ("sequenceID", "\ud800"),
        ("data", None),
        ("data", 1),
    ],
)
def test_mnotify_rejects_a_field_of_the_wrong_type(field, value):
    with pytest.raises(InvalidUssdRequest):
        mnotify.parse(mnotify_body(**{field: value}), JSON)


@pytest.mark.parametrize("field", ["msisdn", "sequenceID"])
def test_mnotify_rejects_a_blank_id(field):
    with pytest.raises(InvalidUssdRequest):
        mnotify.parse(mnotify_body(**{field: ""}), JSON)


# Every codec -----------------------------------------------------------------


def test_registry_holds_each_codec_under_its_name():
    assert {name: type(codec) for name, codec in CODECS.items()} == {
        "africastalking": AfricasTalkingCodec,
        "arkesel": ArkeselCodec,
        "mnotify": MnotifyCodec,
    }
    assert all(codec.name == name for name, codec in CODECS.items())


@pytest.mark.parametrize("codec", list(CODECS.values()), ids=list(CODECS))
@pytest.mark.parametrize(
    "body",
    [b"", b"hello", b"\xff\xfe\x00", b"{", b"[]", b"null", b'"text"', b"[" * 100_000],
    ids=["empty", "words", "binary", "broken", "array", "null", "string", "deep"],
)
def test_junk_bodies_are_rejected(codec, body):
    with pytest.raises(InvalidUssdRequest):
        codec.parse(body, JSON)
