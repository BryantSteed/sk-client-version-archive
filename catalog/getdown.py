"""Parsing and validation for Getdown ``getdown.txt`` and ``digest*.txt`` files.

``getdown.txt`` is a flat ``key = value`` config with ``#`` comments, repeated
keys, and optional ``[platform]`` tags prefixing some values. ``digest.txt`` /
``digest2.txt`` reuse the same ``path = hash`` line shape.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"^\d{14}$")
_HASH_LINE_RE = re.compile(r"^\S.* = [0-9a-fA-F]{32,128}\s*$")


def looks_like_html(text: str) -> bool:
    """True if the body looks like an HTML error/redirect page rather than data."""
    head = text.lstrip()[:512].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<title>" in head


def parse_kv(text: str) -> dict[str, list[str]]:
    """Parse a Getdown-style ``key = value`` file into ``{key: [values]}``.

    Comments (``#``) and blank lines are skipped. Values keep any leading
    ``[platform]`` tag verbatim.
    """
    out: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), []).append(value.strip())
    return out


def get_version(parsed: dict[str, list[str]]) -> str:
    """Return the single ``version`` value, validated as a 14-digit stamp."""
    values = parsed.get("version") or []
    if not values:
        raise ValueError("no 'version' key in getdown.txt")
    version = values[0]
    if not _VERSION_RE.match(version):
        raise ValueError(f"version {version!r} is not a 14-digit YYYYMMDDhhmmss stamp")
    return version


def get_appbase_template(parsed: dict[str, list[str]]) -> str | None:
    """Return the ``appbase`` template (contains ``%VERSION%``), or None."""
    values = parsed.get("appbase") or []
    return values[0] if values else None


def is_valid_getdown(text: str, *, expected_version: str | None = None) -> bool:
    """True if ``text`` parses as a getdown.txt with a valid version.

    When ``expected_version`` is given, the file's own ``version`` must match it.
    """
    if looks_like_html(text):
        return False
    try:
        parsed = parse_kv(text)
        version = get_version(parsed)
    except ValueError:
        return False
    return expected_version is None or version == expected_version


def is_valid_digest(text: str) -> bool:
    """True if ``text`` looks like a Getdown digest manifest (``path = hash`` lines)."""
    if looks_like_html(text):
        return False
    return any(_HASH_LINE_RE.match(line) for line in text.splitlines())
