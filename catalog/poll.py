"""Poll Spiral Knights' Getdown ``latest/`` channel and record any new version.

1. GET ``<base>/latest/getdown.txt`` and read its ``version``.
2. If ``versions/latest/<version>/`` already exists, nothing to do.
3. Otherwise resolve that version's ``appbase`` and capture the three text
   manifests (``getdown.txt``, ``digest.txt``, ``digest2.txt``) into the repo.

``TIMELINE.md`` is regenerated from the ``versions/`` tree every run, and
``state/last-check.json`` is rewritten every run so the commit history doubles
as a liveness signal for the scheduled workflow.

Exit status: 0 on success (including "no change"). 1 if a manifest failed
validation, or if every channel was unreachable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Final, Literal, TypedDict

from .fetch import FetchError, Unavailable, fetch_text
from .getdown import (
    get_appbase_template,
    get_version,
    is_valid_digest,
    is_valid_getdown,
    parse_kv,
)

ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent.parent
VERSIONS_DIR: Final[pathlib.Path] = ROOT / "versions"
STATE_FILE: Final[pathlib.Path] = ROOT / "state" / "last-check.json"
TIMELINE_FILE: Final[pathlib.Path] = ROOT / "TIMELINE.md"

DEFAULT_BASE: Final[str] = "https://gamemedia2.spiralknights.com/spiral"
# Only latest/ is tracked. client/ has been frozen at 20260209004019 (the last
# pre-64-bit build) since Feb 2026 and is not expected to move again; capture it
# by hand with --channels client if that ever changes.
CHANNELS: Final[tuple[str, ...]] = ("latest",)
MANIFESTS: Final[tuple[str, ...]] = ("getdown.txt", "digest.txt", "digest2.txt")

ChannelStatus = Literal["new", "unchanged"]


class ChannelResult(TypedDict):
    """The outcome of checking one channel."""

    channel: str
    version: str
    status: ChannelStatus
    captured: list[str]  # manifest filenames actually written / fetched
    unavailable: list[str]  # manifest filenames that 403'd or 404'd


class VersionEntry(TypedDict):
    """One recorded version, as it appears in ``versions/index.json``."""

    version: str
    channel: str
    released: str | None  # ISO-8601 UTC decoded from the version stamp, or None
    manifests: list[str]  # manifest filenames present on disk, sorted
    path: str  # repo-relative directory, e.g. "versions/latest/20260828143805"


#: ``(channel, message)`` — a channel that could not be checked this run.
ChannelError = tuple[str, str]


class ValidationError(Exception):
    """A fetched manifest did not look like what it claimed to be."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_appbase(channel_getdown: dict[str, list[str]], version: str, base: str) -> str:
    template: str = get_appbase_template(channel_getdown) or f"{base}/%VERSION%"
    appbase: str = template.replace("%VERSION%", version)
    if appbase.startswith("http://"):
        appbase = "https://" + appbase[len("http://") :]
    return appbase.rstrip("/")


def check_channel(channel: str, base: str, *, dry_run: bool) -> ChannelResult:
    """Check one channel. Returns a result dict; writes files unless ``dry_run``."""
    channel_url: str = f"{base}/{channel}/getdown.txt"
    text: str = fetch_text(channel_url)
    if not is_valid_getdown(text):
        raise ValidationError(f"{channel_url} did not return a valid getdown.txt")

    parsed: dict[str, list[str]] = parse_kv(text)
    version: str = get_version(parsed)
    dest: pathlib.Path = VERSIONS_DIR / channel / version

    if dest.exists():
        return ChannelResult(
            channel=channel,
            version=version,
            status="unchanged",
            captured=[],
            unavailable=[],
        )

    appbase: str = _resolve_appbase(parsed, version, base)
    bodies: dict[str, str] = {}  # manifest name -> content, retrieved ones only
    unavailable: dict[str, int] = {}  # manifest name -> HTTP status for 403 / 404
    for name in MANIFESTS:
        url: str = f"{appbase}/{name}"
        try:
            body: str = fetch_text(url)
        except Unavailable as exc:
            unavailable[name] = exc.status
            continue

        if name == "getdown.txt":
            if not is_valid_getdown(body, expected_version=version):
                raise ValidationError(f"{url} version mismatch or not a getdown.txt")
        elif not is_valid_digest(body):
            raise ValidationError(f"{url} does not look like a digest manifest")
        bodies[name] = body

    if "getdown.txt" not in bodies:
        raise ValidationError(f"{appbase}/getdown.txt missing for new version {version}")

    captured: list[str] = list(bodies)  # manifest names actually retrieved
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        for name, body in bodies.items():
            (dest / name).write_text(body, encoding="utf-8", newline="\n")
        if unavailable:
            _write_manifest_notes(dest, appbase, unavailable)

    return ChannelResult(
        channel=channel,
        version=version,
        status="new",
        captured=captured,
        unavailable=sorted(unavailable),
    )


def _write_manifest_notes(dest: pathlib.Path, appbase: str, unavailable: dict[str, int]) -> None:
    date: str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = [
        f"# Capture notes for {dest.name}",
        "",
        f"Captured {date} from {appbase}",
        "",
        "The following manifests were not available at capture time",
        "(SK's CDN returns 403 for paths that do not exist):",
        "",
    ]
    lines += [f"- `{name}` — HTTP {status}" for name, status in sorted(unavailable.items())]
    (dest / "CAPTURE-NOTES.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _release_iso(version: str) -> str | None:
    """Decode a 14-digit ``YYYYMMDDhhmmss`` stamp to an ISO-8601 UTC string."""
    try:
        stamp = dt.datetime.strptime(version, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return stamp.replace(tzinfo=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_versions() -> list[VersionEntry]:
    """Every recorded version, newest first — a pure projection of ``versions/``."""
    entries: list[VersionEntry] = []
    for vdir in VERSIONS_DIR.glob("*/*"):
        if not vdir.is_dir():
            continue
        entries.append(
            VersionEntry(
                version=vdir.name,
                channel=vdir.parent.name,
                released=_release_iso(vdir.name),
                manifests=sorted(p.name for p in vdir.glob("*.txt")),
                path=f"versions/{vdir.parent.name}/{vdir.name}",
            )
        )
    entries.sort(key=lambda e: e["version"], reverse=True)
    return entries


def _write_index(entries: list[VersionEntry]) -> None:
    """Write ``versions/index.json`` — the machine-readable catalog.

    Pure function of ``entries`` (no timestamp), so it only changes when the
    recorded version set changes.
    """
    latest: str | None = next((e["version"] for e in entries if e["channel"] == "latest"), None)
    doc: dict[str, object] = {"latest": latest, "versions": entries}
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    (VERSIONS_DIR / "index.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


_TIMELINE_HEADER: Final[str] = (
    "# Spiral Knights version timeline\n\n"
    "Regenerated from `versions/` on every run, newest first. For programmatic\n"
    "use read [`versions/index.json`](versions/index.json) instead.\n\n"
    "| Released (UTC) | Channel | Version | Manifests |\n"
    "|---|---|---|---|\n"
)


def _write_timeline(entries: list[VersionEntry]) -> None:
    """Regenerate ``TIMELINE.md`` — the human-readable view of the same data."""
    rows: list[str] = []
    for e in entries:
        released = e["released"][:16].replace("T", " ") if e["released"] else "?"
        manifests = ", ".join(m.removesuffix(".txt") for m in e["manifests"])
        rows.append(f"| {released} | `{e['channel']}` | `{e['version']}` | {manifests} |")
    TIMELINE_FILE.write_text(
        _TIMELINE_HEADER + "\n".join(rows) + "\n", encoding="utf-8", newline="\n"
    )


def _write_state(results: list[ChannelResult], errors: list[ChannelError]) -> None:
    channels: dict[str, dict[str, str]] = {}
    for r in results:
        channels[r["channel"]] = {"version": r["version"], "status": r["status"]}
    for channel, message in errors:
        channels.setdefault(channel, {})["error"] = message
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"checked_at": _now(), "channels": channels}
    STATE_FILE.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _emit_output(key: str, value: str) -> None:
    path: str | None = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def _commit_message(results: list[ChannelResult]) -> str:
    new: list[ChannelResult] = [r for r in results if r["status"] == "new"]
    if new:
        parts: str = ", ".join(f"{r['channel']} {r['version']}" for r in new)
        return f"catalog: SK {parts}"
    date: str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    return f"catalog: poll {date} (no change)"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="override the Getdown base URL")
    parser.add_argument(
        "--channels",
        default=",".join(CHANNELS),
        help="comma-separated channels to check",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace = _build_parser().parse_args(argv)
    dry_run: bool = args.dry_run
    base_url: str = args.base_url
    channels: list[str] = [c.strip() for c in args.channels.split(",") if c.strip()]

    results: list[ChannelResult] = []
    fetch_errors: list[ChannelError] = []
    validation_failed: bool = False

    for channel in channels:
        try:
            result: ChannelResult = check_channel(channel, base_url, dry_run=dry_run)
            results.append(result)
            marker: str = {"new": "NEW  ", "unchanged": "  -  "}.get(result["status"], "     ")
            extra: str = f" [{', '.join(result['captured'])}]" if result["captured"] else ""
            print(f"{marker} {channel:8} {result['version']}{extra}")
        except ValidationError as exc:
            validation_failed = True
            fetch_errors.append((channel, f"validation: {exc}"))
            print(f"FAIL  {channel:8} validation error: {exc}", file=sys.stderr)
        except FetchError as exc:
            fetch_errors.append((channel, f"fetch: {exc}"))
            print(f"WARN  {channel:8} unreachable: {exc}", file=sys.stderr)

    if not dry_run:
        entries: list[VersionEntry] = scan_versions()
        _write_index(entries)
        _write_timeline(entries)
        _write_state(results, fetch_errors)
        new_count: int = sum(1 for r in results if r["status"] == "new")
        _emit_output("commit_msg", _commit_message(results))
        _emit_output("new_versions", str(new_count))

    all_unreachable: bool = (
        bool(channels) and len(fetch_errors) == len(channels) and not validation_failed
    )
    if validation_failed:
        print("One or more manifests failed validation.", file=sys.stderr)
        return 1
    if all_unreachable:
        print("Every channel was unreachable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
