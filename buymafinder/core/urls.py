from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_url(url: str) -> str:
    """Return a stable HTTPS URL without fragments or tracking parameters."""
    parsed = urlsplit(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"URL must be absolute: {url!r}")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
        )
    )
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(("https", parsed.netloc.lower(), path, query, ""))


def unique_normalized_urls(urls: Iterable[str]) -> list[str]:
    """Normalize URLs and retain their first-seen order."""
    seen: set[str] = set()
    unique_urls = []
    for url in urls:
        normalized = normalize_url(url)
        if normalized not in seen:
            seen.add(normalized)
            unique_urls.append(normalized)
    return unique_urls
