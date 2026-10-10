import tomllib
from pathlib import Path

CONFIG = tomllib.loads((Path(__file__).parent.parent / "supabase" / "config.toml").read_text())


def test_phone_numbers_are_confirmed_before_sign_in():
    """Account linking trusts the token's phone claim, which is only safe when
    Supabase confirms the number by OTP first. The hosted projects must match."""
    assert CONFIG["auth"]["sms"]["enable_confirmations"] is True
