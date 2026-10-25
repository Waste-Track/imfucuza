"""Fetches a photo from a signed Supabase Storage link.

Defences: the caller has already checked the link against the allow-list
(validation.photo_url_allowed); redirects are NOT followed (a redirect could point
anywhere); the download is streamed and cut off at the size limit; and the whole
fetch has a short timeout so we stay inside the Engine's 8-second budget.
"""
import httpx

from service import PhotoFetchError, PhotoTooLarge
from validation import MAX_BYTES


def make_fetcher(timeout_s=4.0, max_bytes=MAX_BYTES, transport=None):
    client = httpx.Client(timeout=timeout_s, follow_redirects=False, transport=transport)

    def fetch(url):
        try:
            with client.stream('GET', url) as r:
                if r.status_code != 200:
                    raise PhotoFetchError(f'HTTP {r.status_code}')
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise PhotoTooLarge()
                return bytes(buf)
        except httpx.HTTPError as e:
            raise PhotoFetchError(type(e).__name__) from None

    return fetch
