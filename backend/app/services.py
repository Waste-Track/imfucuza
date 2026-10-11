"""The provider adapters this process uses, chosen once at startup.

Job handlers and routes call `get()`. Tests install fakes with `install()`.
"""

from dataclasses import dataclass, field

from app.adapters.base import Classifier, PaymentProvider, PhotoStore, SmsGateway
from app.adapters.fakes import (
    FakeClassifier,
    FakePaymentProvider,
    FakePhotoStore,
    FakeSmsGateway,
)
from app.config import Settings

# Created private by migration 20261014090000_pickup_photos_bucket.sql.
PHOTO_BUCKET = "pickup-photos"


@dataclass
class Services:
    payments: PaymentProvider
    sms: SmsGateway
    photos: PhotoStore = field(default_factory=FakePhotoStore)
    classifier: Classifier = field(default_factory=FakeClassifier)


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

    if settings.photo_store == "supabase":
        from app.adapters.supabase_storage import SupabasePhotoStore

        photos: PhotoStore = SupabasePhotoStore(
            settings.supabase_url,
            settings.supabase_secret_key.get_secret_value(),
            PHOTO_BUCKET,
        )
    else:
        photos = FakePhotoStore()

    if settings.classifier == "http":
        from app.adapters.classifier import HttpClassifier

        classifier: Classifier = HttpClassifier(
            settings.classifier_url, settings.classifier_token.get_secret_value()
        )
    elif settings.classifier == "none":
        from app.adapters.classifier import NoClassifier

        classifier = NoClassifier()
    else:
        classifier = FakeClassifier()
    return Services(payments=payments, sms=sms, photos=photos, classifier=classifier)


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
