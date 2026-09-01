"""Parser/validator tests using real recovered manifests as fixtures."""

from __future__ import annotations

import pathlib

import pytest

from catalog.getdown import (
    get_appbase_template,
    get_version,
    is_valid_digest,
    is_valid_getdown,
    looks_like_html,
    parse_kv,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
GETDOWN_2020: str = (FIXTURES / "getdown-20201019172329.txt").read_text(encoding="utf-8")
GETDOWN_2015: str = (FIXTURES / "getdown-20151118125413.txt").read_text(encoding="utf-8")
DIGEST_2020: str = (FIXTURES / "digest-20201019172329.txt").read_text(encoding="utf-8")


def test_parse_kv_repeated_keys() -> None:
    parsed = parse_kv(GETDOWN_2020)
    assert parsed["version"] == ["20201019172329"]
    assert parsed["code"][0] == "code/config.jar"
    assert len(parsed["code"]) == 11  # 11 classpath jars in this build


def test_parse_kv_skips_comments_and_blanks() -> None:
    parsed = parse_kv("# a comment\n\nversion = 1\n  # indented comment\n")
    assert parsed == {"version": ["1"]}


def test_parse_kv_keeps_platform_tags() -> None:
    parsed = parse_kv(GETDOWN_2020)
    assert any(v.startswith("[windows]") for v in parsed["resource"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [(GETDOWN_2020, "20201019172329"), (GETDOWN_2015, "20151118125413")],
)
def test_get_version(text: str, expected: str) -> None:
    assert get_version(parse_kv(text)) == expected


def test_get_version_rejects_non_stamp() -> None:
    with pytest.raises(ValueError):
        get_version({"version": ["1.2.3"]})


def test_get_version_missing() -> None:
    with pytest.raises(ValueError):
        get_version({})


def test_get_appbase_template() -> None:
    tmpl = get_appbase_template(parse_kv(GETDOWN_2020))
    assert tmpl == "http://gamemedia2.spiralknights.com/spiral/%VERSION%"
    assert tmpl is not None and "%VERSION%" in tmpl


def test_is_valid_getdown_roundtrip() -> None:
    assert is_valid_getdown(GETDOWN_2020)
    assert is_valid_getdown(GETDOWN_2020, expected_version="20201019172329")
    assert not is_valid_getdown(GETDOWN_2020, expected_version="19990101000000")


def test_is_valid_getdown_rejects_html() -> None:
    assert not is_valid_getdown("<!DOCTYPE html><html><title>404</title></html>")


def test_is_valid_digest() -> None:
    assert is_valid_digest(DIGEST_2020)
    first = DIGEST_2020.splitlines()[0]
    assert first == "getdown.txt = e90b0b622a708fa67a7cf3f4489134b2"


def test_is_valid_digest_rejects_prose() -> None:
    assert not is_valid_digest("this is just some text\nwith no hashes\n")


def test_looks_like_html() -> None:
    assert looks_like_html("  <!doctype html>...")
    assert looks_like_html("<html lang='en'>")
    assert not looks_like_html("version = 20201019172329")
