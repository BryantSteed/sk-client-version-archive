# sk-client-catalog

A daily poller that tracks **Spiral Knights** client versions using the game's
own [Getdown](https://github.com/threerings/getdown) update metadata — no game
install, no running process, no login required.

## What it does

A scheduled GitHub Actions workflow runs [`catalog/poll.py`](catalog/poll.py)
once a day:

1. Fetch `https://gamemedia2.spiralknights.com/spiral/latest/getdown.txt` and
   read its `version`.
2. If that version is already recorded under `versions/latest/`, do nothing.
3. Otherwise resolve the version's `appbase` and save its three text manifests:

   ```
   versions/<channel>/<version>/getdown.txt
   versions/<channel>/<version>/digest.txt    # MD5 manifest of every shipped file
   versions/<channel>/<version>/digest2.txt   # SHA-256 manifest of every shipped file
   ```

`versions/index.json` and [`TIMELINE.md`](TIMELINE.md) are then regenerated as
projections of the `versions/` tree. `state/last-check.json` is rewritten every
run, so the commit history is also a liveness record for the scheduled job.

Only the `latest/` channel is tracked. The `client/` channel has been frozen at
`20260209004019` (the last pre-64-bit build) since February 2026; pass
`--channels client` for a one-off capture if it ever moves.

## Consuming the catalog

Everything is plain files on the `main` branch — fetch them over raw HTTP, no
API or auth:

```
https://raw.githubusercontent.com/<owner>/<repo>/main/versions/index.json
```

`index.json` is the machine-readable catalog, newest first:

```json
{
  "latest": "20260828143805",
  "versions": [
    {
      "version": "20260828143805",
      "channel": "latest",
      "released": "2026-08-28T14:38:05Z",
      "manifests": ["digest.txt", "digest2.txt", "getdown.txt"],
      "path": "versions/latest/20260828143805"
    }
  ]
}
```

- `latest` — the newest version on the `latest` channel.
- `versions[]` — every recorded version, sorted newest first. Version strings
  are `YYYYMMDDhhmmss`, so they also sort chronologically.
- `released` — decoded from the version stamp (UTC); `null` if it isn't a stamp.
- `manifests` — which manifest files are present for that version (older builds
  predate `digest2.txt`).
- `path` — repo-relative directory; fetch a manifest at
  `https://raw.githubusercontent.com/<owner>/<repo>/main/<path>/digest2.txt`.

`index.json` has no timestamp of its own — it changes only when the recorded
version set changes, so a diff on it means "new version".

### Scope

This repo stores **only text metadata** — version numbers, URLs, filenames, and
hashes. It does not download, decompile, or redistribute any game code. Anything
involving the actual client jars lives elsewhere and is not public.

## Local use

```bash
python -m catalog.poll --dry-run     # fetch + report, write nothing
python -m catalog.poll               # real run (writes files, updates state)

python -m venv .venv && . .venv/Scripts/activate   # (or .venv/bin/activate)
pip install -e ".[dev]"
pytest                               # offline; uses recovered manifests as fixtures
mypy                                 # strict type check (catalog/ + tests/)
```

`--base-url` and `--channels` override the defaults for testing. CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs `mypy` and
`pytest` on every push and PR.

## Design notes

Architecture background and the reasoning behind the public/private split is in
[`.planning/phase-1-architecture/decisions.md`](.planning/phase-1-architecture/decisions.md).
