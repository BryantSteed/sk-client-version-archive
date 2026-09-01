"""End-to-end poll logic with fetch stubbed out and the repo dirs redirected."""

from __future__ import annotations

import json
import pathlib

import pytest

from catalog import poll
from catalog.fetch import FetchError, Unavailable

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
GETDOWN_2020 = (FIXTURES / "getdown-20201019172329.txt").read_text(encoding="utf-8")
DIGEST_2020 = (FIXTURES / "digest-20201019172329.txt").read_text(encoding="utf-8")

VERSION = "20201019172329"
BASE = "https://example.test/spiral"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(poll, "VERSIONS_DIR", tmp_path / "versions")
    monkeypatch.setattr(poll, "STATE_FILE", tmp_path / "state" / "last-check.json")
    monkeypatch.setattr(poll, "TIMELINE_FILE", tmp_path / "TIMELINE.md")
    return tmp_path


def _fake_fetch(responses: dict[str, str]):
    def _fetch(url, **_kw):
        if url in responses:
            return responses[url]
        raise Unavailable(f"403: {url}", 403)

    return _fetch


def test_new_version_is_captured(repo, monkeypatch):
    responses = {
        f"{BASE}/latest/getdown.txt": GETDOWN_2020,
        f"https://gamemedia2.spiralknights.com/spiral/{VERSION}/getdown.txt": GETDOWN_2020,
        f"https://gamemedia2.spiralknights.com/spiral/{VERSION}/digest.txt": DIGEST_2020,
        # digest2.txt intentionally absent -> 404 -> captured as None
    }
    monkeypatch.setattr(poll, "fetch_text", _fake_fetch(responses))

    result = poll.check_channel("latest", BASE, dry_run=False)

    assert result["status"] == "new"
    assert result["captured"] == ["getdown.txt", "digest.txt"]
    assert result["unavailable"] == ["digest2.txt"]
    vdir = repo / "versions" / "latest" / VERSION
    assert (vdir / "getdown.txt").read_text(encoding="utf-8") == GETDOWN_2020
    assert (vdir / "digest.txt").exists()
    assert not (vdir / "digest2.txt").exists()
    assert "digest2.txt" in (vdir / "CAPTURE-NOTES.md").read_text(encoding="utf-8")
    assert poll.TIMELINE_MARKER in (repo / "TIMELINE.md").read_text(encoding="utf-8")


def test_known_version_is_unchanged(repo, monkeypatch):
    (repo / "versions" / "latest" / VERSION).mkdir(parents=True)
    monkeypatch.setattr(
        poll, "fetch_text", _fake_fetch({f"{BASE}/latest/getdown.txt": GETDOWN_2020})
    )

    result = poll.check_channel("latest", BASE, dry_run=False)

    assert result["status"] == "unchanged"
    assert result["captured"] == []


def test_dry_run_writes_nothing(repo, monkeypatch):
    responses = {
        f"{BASE}/latest/getdown.txt": GETDOWN_2020,
        f"https://gamemedia2.spiralknights.com/spiral/{VERSION}/getdown.txt": GETDOWN_2020,
        f"https://gamemedia2.spiralknights.com/spiral/{VERSION}/digest.txt": DIGEST_2020,
    }
    monkeypatch.setattr(poll, "fetch_text", _fake_fetch(responses))

    result = poll.check_channel("latest", BASE, dry_run=True)

    assert result["status"] == "new"
    assert not (repo / "versions").exists()
    assert not (repo / "TIMELINE.md").exists()


def test_html_error_page_is_rejected(repo, monkeypatch):
    monkeypatch.setattr(
        poll,
        "fetch_text",
        _fake_fetch({f"{BASE}/latest/getdown.txt": "<!doctype html><title>Error</title>"}),
    )
    with pytest.raises(poll.ValidationError):
        poll.check_channel("latest", BASE, dry_run=False)


def test_version_mismatch_in_appbase_is_rejected(repo, monkeypatch):
    wrong = GETDOWN_2020.replace(VERSION, "20991231235959")
    responses = {
        f"{BASE}/latest/getdown.txt": GETDOWN_2020,
        f"https://gamemedia2.spiralknights.com/spiral/{VERSION}/getdown.txt": wrong,
    }
    monkeypatch.setattr(poll, "fetch_text", _fake_fetch(responses))
    with pytest.raises(poll.ValidationError):
        poll.check_channel("latest", BASE, dry_run=False)


def test_main_all_unreachable_exits_1(repo, monkeypatch):
    def _boom(url, **_kw):
        raise FetchError("network down")

    monkeypatch.setattr(poll, "fetch_text", _boom)
    rc = poll.main(["--base-url", BASE, "--channels", "latest,client"])
    assert rc == 1
    state = json.loads(poll.STATE_FILE.read_text(encoding="utf-8"))
    assert "error" in state["channels"]["latest"]


def test_main_unchanged_exits_0_and_writes_state(repo, monkeypatch):
    (repo / "versions" / "latest" / VERSION).mkdir(parents=True)
    (repo / "versions" / "client" / VERSION).mkdir(parents=True)
    monkeypatch.setattr(
        poll,
        "fetch_text",
        _fake_fetch(
            {
                f"{BASE}/latest/getdown.txt": GETDOWN_2020,
                f"{BASE}/client/getdown.txt": GETDOWN_2020,
            }
        ),
    )
    rc = poll.main(["--base-url", BASE, "--channels", "latest,client"])
    assert rc == 0
    state = json.loads(poll.STATE_FILE.read_text(encoding="utf-8"))
    assert state["channels"]["latest"]["status"] == "unchanged"
    assert state["checked_at"].endswith("Z")
