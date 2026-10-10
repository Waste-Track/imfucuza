"""The provider adapters this process uses, chosen once at startup.

Job handlers and routes call `get()`. Tests install fakes with `install()`.
"""

from dataclasses import dataclass

from app.adapters.base import PaymentProvider, SmsGateway
from app.adapters.fakes import FakePaymentProvider, FakeSmsGateway
from app.config import Settings


@dataclass
class Services:
    payments: PaymentProvider
    sms: SmsGateway


_current: Services | None = None


def build(settings: Settings) -> Services:
    if settings.payment_provider == "paystack":
        from app.adapters.paystack import PaystackProvider

        payments: PaymentProvider = PaystackProvider(
            settings.paystack_secret_key.get_secret_value()
        )
    else:
        payments = FakePaymentProvider()

    if settings.sms_gateway == "mnotify":
        from app.adapters.mnotify import MnotifyGateway

        sms: SmsGateway = MnotifyGateway(
            settings.mnotify_api_key.get_secret_value(), sender_id=settings.sms_sender_id
        )
    else:
        sms = FakeSmsGateway()
    return Services(payments=payments, sms=sms)


def ensure(settings: Settings) -> Services:
    """Install the configured adapters unless a test already installed some."""
    if _current is None:
        install(build(settings))
    return get()


def install(services: Services) -> None:
    global _current
    _current = services


def uninstall() -> None:
    global _current
    _current = None


def get() -> Services:
    if _current is None:
        raise RuntimeError("services are not installed: the app lifespan installs them")
    return _current
