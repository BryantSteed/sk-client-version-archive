"""Poll Spiral Knights' Getdown channels and record any new client versions.

For each tracked channel (``latest``, ``client``):

1. GET ``<base>/<channel>/getdown.txt`` and read its ``version``.
2. If ``versions/<channel>/<version>/`` already exists, nothing to do.
3. Otherwise resolve that version's ``appbase`` and capture the three text
   manifests (``getdown.txt``, ``digest.txt``, ``digest2.txt``) into the repo,
   then append a row to ``TIMELINE.md``.

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

from .fetch import FetchError, Unavailable, fetch_text
from .getdown import (
    get_appbase_template,
    get_version,
    is_valid_digest,
    is_valid_getdown,
    parse_kv,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSIONS_DIR = ROOT / "versions"
STATE_FILE = ROOT / "state" / "last-check.json"
TIMELINE_FILE = ROOT / "TIMELINE.md"
TIMELINE_MARKER = "<!-- rows -->"

DEFAULT_BASE = "https://gamemedia2.spiralknights.com/spiral"
CHANNELS = ("latest", "client")
MANIFESTS = ("getdown.txt", "digest.txt", "digest2.txt")


class ValidationError(Exception):
    """A fetched manifest did not look like what it claimed to be."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_appbase(channel_getdown: dict[str, list[str]], version: str, base: str) -> str:
    template = get_appbase_template(channel_getdown) or f"{base}/%VERSION%"
    appbase = template.replace("%VERSION%", version)
    if appbase.startswith("http://"):
        appbase = "https://" + appbase[len("http://") :]
    return appbase.rstrip("/")


def check_channel(channel: str, base: str, *, dry_run: bool) -> dict:
    """Check one channel. Returns a result dict; may write files unless dry_run."""
    channel_url = f"{base}/{channel}/getdown.txt"
    text = fetch_text(channel_url)
    if not is_valid_getdown(text):
        raise ValidationError(f"{channel_url} did not return a valid getdown.txt")

    parsed = parse_kv(text)
    version = get_version(parsed)
    dest = VERSIONS_DIR / channel / version

    if dest.exists():
        return {
            "channel": channel,
            "version": version,
            "status": "unchanged",
            "captured": [],
            "unavailable": [],
        }

    appbase = _resolve_appbase(parsed, version, base)
    captured: dict[str, str | None] = {}
    unavailable: dict[str, int] = {}
    for name in MANIFESTS:
        url = f"{appbase}/{name}"
        try:
            body = fetch_text(url)
        except Unavailable as exc:
            captured[name] = None
            unavailable[name] = exc.status
            continue

        if name == "getdown.txt":
            if not is_valid_getdown(body, expected_version=version):
                raise ValidationError(f"{url} version mismatch or not a getdown.txt")
        elif not is_valid_digest(body):
            raise ValidationError(f"{url} does not look like a digest manifest")
        captured[name] = body

    if captured.get("getdown.txt") is None:
        raise ValidationError(f"{appbase}/getdown.txt missing for new version {version}")

    got = [n for n, v in captured.items() if v is not None]
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        for name, body in captured.items():
            if body is not None:
                (dest / name).write_text(body, encoding="utf-8", newline="\n")
        if unavailable:
            _write_manifest_notes(dest, appbase, unavailable)
        _append_timeline(channel, version, got)

    return {
        "channel": channel,
        "version": version,
        "status": "new",
        "captured": got,
        "unavailable": sorted(unavailable),
    }


def _write_manifest_notes(dest: pathlib.Path, appbase: str, unavailable: dict[str, int]) -> None:
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    lines = [
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


def _append_timeline(channel: str, version: str, manifests: list[str]) -> None:
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    short = ", ".join(m.removesuffix(".txt") for m in manifests)
    row = f"| {date} | `{channel}` | `{version}` | {short} |\n"

    if not TIMELINE_FILE.exists():
        TIMELINE_FILE.write_text(_TIMELINE_HEADER, encoding="utf-8", newline="\n")

    lines = TIMELINE_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == TIMELINE_MARKER:
            lines.insert(i + 1, row)
            break
    else:  # no marker — append at end
        lines.append(row)
    TIMELINE_FILE.write_text("".join(lines), encoding="utf-8", newline="\n")


_TIMELINE_HEADER = f"""# Spiral Knights version timeline

Every client version seen by the daily poller, newest first. Each version's
`getdown.txt` / `digest.txt` / `digest2.txt` are stored under `versions/`.

| First seen (UTC) | Channel | Version | Manifests |
|---|---|---|---|
{TIMELINE_MARKER}
"""


def _write_state(results: list[dict], errors: list[tuple[str, str]]) -> None:
    channels: dict[str, dict] = {}
    for r in results:
        channels[r["channel"]] = {"version": r["version"], "status": r["status"]}
    for channel, message in errors:
        channels.setdefault(channel, {})["error"] = message
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"checked_at": _now(), "channels": channels}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _emit_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def _commit_message(results: list[dict]) -> str:
    new = [r for r in results if r["status"] == "new"]
    if new:
        parts = ", ".join(f"{r['channel']} {r['version']}" for r in new)
        return f"catalog: SK {parts}"
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    return f"catalog: poll {date} (no change)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="override the Getdown base URL")
    parser.add_argument(
        "--channels",
        default=",".join(CHANNELS),
        help="comma-separated channels to check",
    )
    args = parser.parse_args(argv)
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]

    results: list[dict] = []
    validation_failed = False
    fetch_errors: list[tuple[str, str]] = []

    for channel in channels:
        try:
            result = check_channel(channel, args.base_url, dry_run=args.dry_run)
            results.append(result)
            marker = {"new": "NEW  ", "unchanged": "  -  "}.get(result["status"], "     ")
            extra = f" [{', '.join(result['captured'])}]" if result["captured"] else ""
            print(f"{marker} {channel:8} {result['version']}{extra}")
        except ValidationError as exc:
            validation_failed = True
            fetch_errors.append((channel, f"validation: {exc}"))
            print(f"FAIL  {channel:8} validation error: {exc}", file=sys.stderr)
        except FetchError as exc:
            fetch_errors.append((channel, f"fetch: {exc}"))
            print(f"WARN  {channel:8} unreachable: {exc}", file=sys.stderr)

    if not args.dry_run:
        _write_state(results, fetch_errors)
        _emit_output("commit_msg", _commit_message(results))
        _emit_output("new_versions", str(sum(1 for r in results if r["status"] == "new")))

    all_unreachable = bool(channels) and len(fetch_errors) == len(channels) and not validation_failed
    if validation_failed:
        print("One or more manifests failed validation.", file=sys.stderr)
        return 1
    if all_unreachable:
        print("Every channel was unreachable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
