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

   and append a row to [`TIMELINE.md`](TIMELINE.md).

Only the `latest/` channel is tracked. The `client/` channel has been frozen at
`20260209004019` (the last pre-64-bit build) since February 2026; pass
`--channels client` for a one-off capture if it ever moves.

`state/last-check.json` is rewritten every run, so the commit history is also a
liveness record for the scheduled job.

### Scope

This repo stores **only text metadata** — version numbers, URLs, filenames, and
hashes. It does not download, decompile, or redistribute any game code. Anything
involving the actual client jars lives elsewhere and is not public.

## Local use

```bash
python -m catalog.poll --dry-run     # fetch + report, write nothing
python -m catalog.poll               # real run (writes files, updates state)

pip install -e ".[dev]"
pytest                               # offline; uses recovered manifests as fixtures
```

`--base-url` and `--channels` override the defaults for testing.

## Design notes

Architecture background and the reasoning behind the public/private split is in
[`.planning/phase-1-architecture/decisions.md`](.planning/phase-1-architecture/decisions.md).
