"""Minimal HTTP text fetcher with retry and an identifiable User-Agent.

Spiral Knights' Getdown endpoints are plain, unauthenticated HTTP. We only ever
GET small text manifests, once a day. The User-Agent names this project so SK's
operators can see exactly what the traffic is if they ever look.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from typing import Final

from . import __version__

# In GitHub Actions GITHUB_REPOSITORY is "owner/repo"; locally it falls back to a
# harmless placeholder. The UA names the project so SK operators can see what the
# once-a-day traffic is.
_REPO: Final[str] = os.environ.get("GITHUB_REPOSITORY", "OWNER/sk-client-catalog")
USER_AGENT: Final[str] = f"sk-client-catalog/{__version__} (+https://github.com/{_REPO})"

#: HTTP status codes that mean "this resource does not exist and won't on retry".
#: SK's CDN uses 403 (not 404) for missing paths, so both are treated the same.
_MISSING_STATUS: Final[frozenset[int]] = frozenset({403, 404})


class FetchError(Exception):
    """A URL could not be retrieved after retries."""


class Unavailable(FetchError):
    """The resource is not retrievable and won't be on retry (404, or SK's 403).

    Spiral Knights' CDN returns 403 rather than 404 for paths that don't exist
    (e.g. ``digest2.txt`` on builds that predate it), so both are treated as
    "this manifest is simply not available for this version".
    """

    status: int

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def fetch_text(
    url: str,
    *,
    retries: int = 4,
    backoff: float = 2.0,
    timeout: float = 30.0,
) -> str:
    """GET ``url`` and return the decoded body.

    Retries on network errors and 5xx responses with exponential backoff.
    Raises :class:`Unavailable` on 403/404, :class:`FetchError` on any other
    failure (including other 4xx and exhausted retries).
    """
    last_exc: Exception | None = None
    req: urllib.request.Request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset: str = resp.headers.get_content_charset() or "utf-8"
                raw: bytes = resp.read()
                return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            status: int = exc.code
            if status in _MISSING_STATUS:
                raise Unavailable(f"HTTP {status} for {url}", status) from exc
            last_exc = exc
            if status < 500:
                # Other 4xx won't fix itself on retry.
                raise FetchError(f"HTTP {status} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc

        if attempt < retries - 1:
            delay: float = backoff * (2**attempt)
            time.sleep(delay)

    raise FetchError(f"failed to fetch {url} after {retries} attempts: {last_exc}")
