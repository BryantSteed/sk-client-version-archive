"""End-to-end poll logic with fetch stubbed out and the repo dirs redirected."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

import pytest

from catalog import poll
from catalog.fetch import FetchError, Unavailable

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
GETDOWN_2020: str = (FIXTURES / "getdown-20201019172329.txt").read_text(encoding="utf-8")
DIGEST_2020: str = (FIXTURES / "digest-20201019172329.txt").read_text(encoding="utf-8")

VERSION = "20201019172329"
BASE = "https://example.test/spiral"
APPBASE = f"https://gamemedia2.spiralknights.com/spiral/{VERSION}"

FetchStub = Callable[..., str]


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.setattr(poll, "VERSIONS_DIR", tmp_path / "versions")
    monkeypatch.setattr(poll, "STATE_FILE", tmp_path / "state" / "last-check.json")
    monkeypatch.setattr(poll, "TIMELINE_FILE", tmp_path / "TIMELINE.md")
    return tmp_path


def _fake_fetch(responses: dict[str, str]) -> FetchStub:
    def _fetch(url: str, **_kw: object) -> str:
        if url in responses:
            return responses[url]
        raise Unavailable(f"403: {url}", 403)

    return _fetch


def test_new_version_is_captured(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        f"{BASE}/latest/getdown.txt": GETDOWN_2020,
        f"{APPBASE}/getdown.txt": GETDOWN_2020,
        f"{APPBASE}/digest.txt": DIGEST_2020,
        # digest2.txt intentionally absent -> 403 -> captured as None
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


def test_known_version_is_unchanged(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "versions" / "latest" / VERSION).mkdir(parents=True)
    monkeypatch.setattr(
        poll, "fetch_text", _fake_fetch({f"{BASE}/latest/getdown.txt": GETDOWN_2020})
    )

    result = poll.check_channel("latest", BASE, dry_run=False)

    assert result["status"] == "unchanged"
    assert result["captured"] == []


def test_dry_run_writes_nothing(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        f"{BASE}/latest/getdown.txt": GETDOWN_2020,
        f"{APPBASE}/getdown.txt": GETDOWN_2020,
        f"{APPBASE}/digest.txt": DIGEST_2020,
    }
    monkeypatch.setattr(poll, "fetch_text", _fake_fetch(responses))

    result = poll.check_channel("latest", BASE, dry_run=True)

    assert result["status"] == "new"
    assert not (repo / "versions").exists()
    assert not (repo / "TIMELINE.md").exists()


def test_html_error_page_is_rejected(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        poll,
        "fetch_text",
        _fake_fetch({f"{BASE}/latest/getdown.txt": "<!doctype html><title>Error</title>"}),
    )
    with pytest.raises(poll.ValidationError):
        poll.check_channel("latest", BASE, dry_run=False)


def test_version_mismatch_in_appbase_is_rejected(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = GETDOWN_2020.replace(VERSION, "20991231235959")
    responses = {
        f"{BASE}/latest/getdown.txt": GETDOWN_2020,
        f"{APPBASE}/getdown.txt": wrong,
    }
    monkeypatch.setattr(poll, "fetch_text", _fake_fetch(responses))
    with pytest.raises(poll.ValidationError):
        poll.check_channel("latest", BASE, dry_run=False)


def test_index_and_timeline_regenerate_from_versions(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs = {
        "20260828143805": ("getdown.txt", "digest.txt", "digest2.txt"),
        "20260807115345": ("getdown.txt", "digest.txt"),
    }
    for version, manifests in specs.items():
        vdir = repo / "versions" / "latest" / version
        vdir.mkdir(parents=True)
        for manifest in manifests:
            (vdir / manifest).write_text("x", encoding="utf-8")

    entries = poll.scan_versions()
    poll._write_index(entries)
    poll._write_timeline(entries)

    index = json.loads((repo / "versions" / "index.json").read_text(encoding="utf-8"))
    assert index["latest"] == "20260828143805"
    assert [e["version"] for e in index["versions"]] == ["20260828143805", "20260807115345"]

    newest = index["versions"][0]
    assert newest["channel"] == "latest"
    assert newest["released"] == "2026-08-28T14:38:05Z"
    assert newest["manifests"] == ["digest.txt", "digest2.txt", "getdown.txt"]
    assert newest["path"] == "versions/latest/20260828143805"

    timeline = (repo / "TIMELINE.md").read_text(encoding="utf-8")
    assert "`20260828143805`" in timeline
    assert "2026-08-28 14:38" in timeline

    # regeneration is a pure function of versions/ — byte-identical on a re-run
    before = (repo / "versions" / "index.json").read_bytes()
    poll._write_index(poll.scan_versions())
    assert (repo / "versions" / "index.json").read_bytes() == before


def test_main_all_unreachable_exits_1(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(url: str, **_kw: object) -> str:
        raise FetchError("network down")

    monkeypatch.setattr(poll, "fetch_text", _boom)
    rc = poll.main(["--base-url", BASE, "--channels", "latest,client"])
    assert rc == 1
    state = json.loads(poll.STATE_FILE.read_text(encoding="utf-8"))
    assert "error" in state["channels"]["latest"]


def test_main_unchanged_exits_0_and_writes_state(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
