from uuid import uuid4

import pytest

from app.domain.pins import _is_trivial, generate_pin, pin_hmac


@pytest.mark.parametrize("pin", ["000000", "777777", "123456", "456789", "987654", "012345"])
def test_trivial_pins_are_recognised(pin):
    assert _is_trivial(pin)


def test_generated_pins_are_six_digits_and_never_trivial():
    for _ in range(500):
        pin = generate_pin()
        assert len(pin) == 6 and pin.isdigit()
        assert not _is_trivial(pin)


def test_the_same_pin_hashes_differently_for_another_pickup():
    assert pin_hmac(uuid4(), "483119") != pin_hmac(uuid4(), "483119")
