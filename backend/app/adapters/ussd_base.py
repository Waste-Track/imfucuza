"""USSD: one request per keypress, answered within the provider's timeout.

Every provider frames a session differently. A codec turns its request into a
UssdRequest and our reply back into its format, so the menus never see the
difference.
"""

from dataclasses import dataclass
from typing import Protocol


class InvalidUssdRequest(Exception):
    pass


@dataclass(frozen=True)
class UssdRequest:
    provider: str
    session_id: str
    # As the provider sent it. The route normalises it to E.164.
    msisdn: str
    # Only what the user typed on this screen ("" on the first request).
    input: str
    is_new: bool
    # How many inputs the session has had, if the provider says. Lets a resent
    # request be recognised and answered without acting twice.
    step: int | None = None


@dataclass(frozen=True)
class UssdReply:
    text: str
    # True ends the session. False shows the text and waits for input.
    end: bool


class UssdCodec(Protocol):
    name: str

    def parse(self, body: bytes, content_type: str) -> UssdRequest:
        """Raises InvalidUssdRequest if the body isn't a request we understand."""
        ...

    def render(self, request: UssdRequest, reply: UssdReply) -> tuple[bytes, str]:
        """Our reply as the provider expects it: body bytes and media type."""
        ...
