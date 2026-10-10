import pytest

from app.domain.people import normalize_msisdn


@pytest.mark.parametrize(
    "raw",
    ["0241234567", "024 123 4567", "233241234567", "+233241234567", "00233241234567"],
)
def test_ghanaian_numbers_normalise_to_e164(raw):
    assert normalize_msisdn(raw) == "+233241234567"


@pytest.mark.parametrize("raw", ["", "12", "not a number", "+0241234567"])
def test_invalid_numbers_are_rejected(raw):
    with pytest.raises(ValueError):
        normalize_msisdn(raw)
