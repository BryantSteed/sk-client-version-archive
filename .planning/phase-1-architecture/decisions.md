# SK Reverse Engineering Pipeline — Architecture Decisions

Status: planning, no implementation started yet.

## Starting point

Existing scripts in this repo (`decompile_sk.py`, `format_java_exec.py`,
`compute_launch.ps1`) work by finding the *currently running* Spiral Knights
java process via WMI, parsing its `-classpath` argument, and decompiling each
jar with Vineflower. Windows-only, requires the game to be installed and
running locally, single hardcoded decompiler.

Goal: something that can be refreshed easily whenever a new game version
ships, and is usable from other repos/tools.

## Key discovery: Getdown metadata makes the live process unnecessary

Spiral Knights uses Getdown for updates. The local install's
`app/getdown.txt` contains everything needed to fetch a version's files
without the game running:

```
version = 20260812101850
appbase = http://gamemedia2.spiralknights.com/spiral/%VERSION%
latest  = http://gamemedia2.spiralknights.com/spiral/latest/getdown.txt
code = code/projectx-pcode.jar
...
```

`app/digest.txt` / `digest2.txt` list per-file hashes for the version.

So the pipeline can just:
1. Fetch `latest/getdown.txt` remotely.
2. Read `version`.
3. Build `appbase` for that version and download `digest.txt` + each listed
   `code/*.jar`.

No process inspection, no local install required, works in CI.

## Decision: separate "catalog" tool from "decompile" tool

Decompilation is a **consumer-side concern**, not a fact about a release —
different decompilers (Vineflower, CFR, Procyon, Fernflower) suit different
classes/needs, especially for modding work. Baking one decompiler into the
version-tracking tool couples "did the version change" to one tool's opinion
of the bytecode, and makes the catalog tool responsible for something that
varies per-user.

**`sk-client-catalog`** (build-the-repo side, robust, runs in CI):
- `check` — poll `latest/getdown.txt`, compare `version` to last recorded.
- `sync` — on a new version: download the `code/*.jar` set, publish the
  compiled jars as-is to a Maven repo (see below), and commit an unpacked
  `.class` file list / per-class digest manifest to a git history repo so
  `git diff` between two versions cheaply shows which classes were
  added/removed/changed — without needing a decompile step to know "what
  did this patch touch."
- No decompiler dependency at all.
- Intended to run on a schedule via GitHub Actions.

**Dev-side decompile tool** (use-the-repo side, scrappier, local):
- Pulls a given version's jars from the catalog's Maven repo (or works on
  jars already on disk).
- Runs whatever decompiler the user wants against them, Vineflower as
  default but pluggable.
- Doesn't need to be robust/CI-grade — this is where `decompile_sk.py`'s
  existing Vineflower logic effectively moves to, decoupled from live
  process sniffing.

This also naturally avoids hammering Spiral Knights' update servers if
multiple people end up using this: only the catalog tool (one scheduled job)
polls the origin; everyone else syncs from the published Maven repo.

## Decision: where to publish — not Maven Central

Considered a global public Maven repo (Sonatype Central) vs GitHub-hosted
options.

**Ruled out Maven Central**, even under a namespace we'd own
(`io.github.<user>`):
- Publishing there means redistributing Spiral Knights' proprietary
  compiled client (and potentially decompiled source) to the world's
  default public Java package index — permanently mirrored, effectively
  undeletable once published, high-visibility target for takedown
  attention. Different risk profile than keeping research artifacts on
  infrastructure we control and can pull down on request.

**Considered GitHub Pages as a static flat Maven repo:**
- Just a directory tree served over plain HTTP — zero auth, works from
  `curl`/browser/Gradle with no token needed.
- Downside: doesn't matter much for this project's actual audience.

**Considered GitHub Packages (Maven registry):**
- Real Maven registry at `maven.pkg.github.com/OWNER/REPO`. Publishing via
  `maven-publish` + `GITHUB_TOKEN` in Actions (no extra secret plumbing).
- Package visibility inherits repo visibility. For a public repo, package
  is public — anyone can pull it, but (unlike Pages) every consumer needs
  an authenticated GitHub PAT (`read:packages` scope) even though it's
  public. This is a GitHub-wide policy for all Packages ecosystems except
  the container registry (`ghcr.io`), not something we configure — no
  per-user allowlist to maintain for a public package.
- Gives a nicer versioned/browsable package history than raw Pages files,
  integrates with Actions with no secret setup.

**Decision: use GitHub Packages, keep it on personal/org-owned
infrastructure (not Central).** The PAT requirement was evaluated and
judged acceptable — the actual audience (Java/Gradle devs) already has
GitHub accounts and is comfortable minting a token. Skipped the
dual-publish-to-Pages idea for now; can revisit if truly anonymous
`curl`-style access is ever needed.

(Superseded below — see "Public vs. private artifacts" — the package
itself ended up needing to be **private**, not just "not Central".)

## Plugin-family model

Realization: the catalog tool should just be a source of version data
(compiled jars + structural metadata per version) with no opinion about
what's done with it. Decompilation especially is a **consumer-side
concern** — different decompilers (Vineflower, CFR, Procyon, Fernflower)
suit different classes/needs, particularly for modding, so baking one into
the catalog tool would wrongly couple "did the version change" to one
tool's opinion of the bytecode.

Instead: several independent, separately-versioned plugins/tools sit on
top of the catalog, each doing one job —
- **decompile plugin** — pulls a version's jars, runs a decompiler of
  choice, produces sources.
- **changelog/diff plugin** — turns the class-manifest history into a
  human-readable "what changed between v1 and v2" report.
- **API-surface / stub plugin** (possible future) — stripped public-API-only
  jar for compiling mods against, without shipping full internals.
- **IDE source-attachment plugin** — wires up sources jars for navigation.

None of these need to know how the catalog gets its data; they just consume
`groupId:artifactId:version` off the Maven repo. New use cases become new
plugins, without touching the catalog tool.

Since plugins are original tooling/code (not a redistribution of the
game's proprietary jars), there's no reason they couldn't be published
publicly/broadly (Gradle Plugin Portal, Maven Central, etc.) even though
the artifact registry itself can't be — see below.

## Legal analysis: is redistributing the client jars (even privately-hosted) OK?

Raised the question of whether hosting on GitHub Packages under a personal
account, instead of Maven Central, meaningfully changes the legal picture.

**Core point: hosting choice changes exposure/visibility/practicality, not
the underlying legality.** These are compiled binaries (and, if decompiled,
arguably derivative source) of a proprietary commercial game. Redistributing
them to other people is redistribution regardless of which product does the
hosting — GitHub Packages is still subject to DMCA takedown like any other
repo content. What *does* change with hosting choice: private repos are a
materially smaller act than public ones (limited audience vs. anyone with a
GitHub account), and non-Central hosting avoids permanent global mirroring.

**Comparison to Minecraft modding (Forge/NeoForge/Fabric)** — raised as
"people do this all the time," investigated and confirmed via
[Forge Community Wiki – Toolchain](https://forge.gemwire.uk/wiki/Toolchain)
and [NeoGradle docs](https://docs.neoforged.net/toolchain/docs/plugins/ng/):

- Forge/NeoForge do **not** distribute a pre-patched Minecraft binary. Their
  Gradle plugins (ForgeGradle/NeoGradle) resolve Mojang's public version
  manifest (`piston-meta.mojang.com`), download the vanilla jar **directly
  from Mojang** per-user, then apply binary patches (`BinaryPatcher`)
  *locally* to produce the patched jar. What they publish via their own
  Maven is the diff/patch data and original loader/mod-loader code, not the
  resulting binary.
- Fabric goes further: no persisted patched binary ever exists — Mixin does
  in-memory bytecode weaving at class-load time.
- A patch/diff, standing alone, doesn't reproduce the copyrighted content —
  it's only meaningful combined with a copy the *user* already legitimately
  obtained elsewhere. Same logic as ROM-hacking communities distributing
  IPS/BPS patches instead of patched ROMs.
- Some of this (patcher/loader code, mapping names) would likely survive a
  derivative-work challenge on its own merits (short identifiers generally
  aren't independently copyrightable; runtime bytecode manipulation doesn't
  embed the original's protected expression). But **the actual load-bearing
  fact making the whole Minecraft modding ecosystem legally comfortable is
  that Mojang explicitly, affirmatively permits modding/third-party tools**
  in their usage guidelines. That permission is what matters most, not the
  cleverness of the patch-based architecture. No known equivalent permission
  exists for Spiral Knights.

**Why the patch-chain idea doesn't transfer to Spiral Knights:** the
Forge pattern is only low-risk *because* Mojang keeps every historical
version permanently addressable on their own servers — a Forge patch is
never the sole remaining source of a given vanilla jar. Confirmed with a
maintainer of the open-source "Knight Launcher" project that Spiral
Knights' origin (`gamemedia2.spiralknights.com`) **only keeps the 5 most
recent versions accessible** — older versions are pruned. That's a
deliberate policy signal that they don't intend old client versions to
remain permanently available.

**Consequence:** past that 5-version window, anything held — full jars
*or* a diff/patch chain — is the sole remaining copy. The diff-vs-full-jar
distinction stops mattering for risk purposes once there's no permanent
origin underneath it. So a **public** diff/patch catalog is not meaningfully
safer than a public full-jar catalog for Spiral Knights, unlike for
Minecraft. Conclusion: **a public diffed/patch-based jar catalog is not a
good idea** — same exposure class as publishing jars directly.

## Public vs. private artifacts — final split

The original point of this project is to be a genuinely usable
deobfuscation/decompile helper with real content behind it, not just a
version-number tracker — so "public catalog, metadata only" was a downgrade
from the actual goal, not the plan. Given the legal analysis above, the
resolution is to split by content type rather than water down the goal:

- **Public** (safe — it's tooling/code and bare facts, not the copyrighted
  content): the catalog CLI itself, the decompile/changelog/etc. plugins,
  the version-number timeline, and structural diff metadata (which classes
  changed) — none of this requires distributing the actual bytes.
- **Private** (the actual deobfuscation-helper payload): the Maven
  repo/package hosting real compiled jars, any decompiled sources, any
  diff/patch data with real content. This is the part that needed to move
  off "public GitHub Packages" — it should be a **private** GitHub
  repo/package, with specific trusted collaborators added explicitly if
  wanted (e.g. other SK reverse-engineering folks), rather than open to
  anyone with a GitHub PAT.
- Because SK only retains 5 versions at the origin, the "sync promptly"
  requirement from the original goal ("refresh when new game versions
  happen") is now load-bearing, not just a nice-to-have: polling cadence
  needs to comfortably beat SK's release cadence (still unknown — worth
  determining) or versions are permanently lost once they roll out of the
  5-version window, with no way to recover them later.

## Two separate getdown channels: `latest/` vs `client/`

Fetched both and compared:

- `https://gamemedia2.spiralknights.com/spiral/latest/getdown.txt` —
  `version = 20260812101850`, self-referential `latest` field pointing back
  at `http://gamemedia2.spiralknights.com/spiral/latest/getdown.txt`,
  matches the local install exactly. `java_version = 25000000` (bundled
  per-platform JRE), Steam API integration, LWJGL 3 natives. This is the
  actively-advancing channel. `appbase = http://gamemedia2.spiralknights.com/spiral/%VERSION%`.
- `https://gamemedia2.spiralknights.com/spiral/client/getdown.txt` —
  `version = 20260209004019` (Feb 9, 2026), also self-referential, pointing
  back at `http://gamemedia2.spiralknights.com/spiral/client/getdown.txt`,
  but frozen — no movement since. Java 1.6+ requirement, no bundled JRE, no
  Steam API, older LWJGL. Looks like the last build before a
  32-bit-to-64-bit JVM overhaul (the user recalls this overhaul happening
  recently; exact Java version bump not confirmed before vs. after, but the
  `latest/` channel's `java_version = 25000000` is consistent with a
  64-bit-only modern JRE). Same `appbase` templating pattern:
  `http://gamemedia2.spiralknights.com/spiral/%VERSION%`.

At least one version of the "Knight Launcher" open-source project (per its
lead maintainer, who's also who confirmed the 5-version retention window
earlier) was observed pointing at `client/` — unclear whether current
Knight Launcher releases have since moved to `latest/`; not established
either way, don't assume it's behind.

**Read: not orphaned/dead infrastructure — likely a deliberate legacy
channel**, frozen at the last pre-overhaul (32-bit) build so systems/tooling
not ready for the 64-bit-only build still have something valid to point at.
Same pattern as keeping an "oldstable" branch through a breaking platform
change.

**Consequence for the catalog tool:** treat these as **two separate tracked
lineages**, not one canonical endpoint with an alias:
- `latest/` — poll regularly (this is the "refresh on new game versions"
  target from the original goal).
- `client/` — capture once as a standalone snapshot rather than polling
  frequently; it may never update again now that the overhaul has happened,
  but what's there now is likely the permanent last snapshot of the
  pre-overhaul client architecture, which has its own historical value
  distinct from `latest/`'s ongoing patch history.
- Also means the earlier "5-version retention" finding may behave
  differently per channel — worth checking whether `client/`'s retention
  window behaves the same way, or whether it's just permanently serving one
  frozen version now.

## `appbase` fetch mechanics (confirmed live)

Full algorithm, confirmed by actually fetching a version-specific URL:

1. Fetch `latest/getdown.txt` (see channels above), read `version` (e.g.
   `20260812101850`).
2. Substitute into the `appbase` template —
   `appbase = http://gamemedia2.spiralknights.com/spiral/%VERSION%` —
   giving that version's base URL, e.g.
   `http://gamemedia2.spiralknights.com/spiral/20260812101850`.
3. Every shipped file is just `<appbase>/<relative path>`, where the
   relative paths are the `code = code/xxx.jar` and `resource = ...` lines
   from `getdown.txt` itself. E.g.
   `http://gamemedia2.spiralknights.com/spiral/20260812101850/code/projectx-pcode.jar`.
   Confirmed live: fetched
   `http://gamemedia2.spiralknights.com/spiral/20260812101850/digest.txt`
   and it exactly matched the local install's `digest.txt` content — plain
   HTTPS GET, no auth, no session, works with the game not running at all.
4. Platform-tagged `code = [windows] code/foo.jar` lines just filter *which*
   files to include for a given target platform; fetch mechanics are the
   same relative-path-off-`appbase` GET either way.

So `check`/`sync` reduces to: fetch `latest/getdown.txt` → diff `version`
against last known → if changed, build that version's `appbase`, fetch its
`digest.txt`/`digest2.txt`, then fetch each relevant `code/*.jar` (filtered
per the proprietary-only decision above) and verify against the listed
hash.

### digest.txt / digest2.txt: hash algorithms and the self-reference quirk

- `digest.txt` uses **MD5** (32 hex chars / 128 bits) — confirmed both by
  counting hex chars and independently by the user.
- `digest2.txt` uses **SHA-256** (64 hex chars / 256 bits) — same file
  list, stronger hash, added by Getdown as the modern alternative to MD5.
  Example, same file, both digests:
  ```
  digest.txt:  code/projectx-pcode.jar = 84837afdf2b738452e86d1ce4478dddc
  digest2.txt: code/projectx-pcode.jar = 333a5926f027ad5f0247e28fff3e5550bb7f3ae28f11c5904f2d4b1486a4825c
  ```
- Either digest file is a complete manifest of every downloadable file in
  that version (every jar, native, UI asset, audio bundle) with hash +
  relative path — useful as a cheap first-pass diff: comparing
  `digest2.txt` between two versions tells you exactly which *files*
  changed, by hash, without needing to unzip/hash individual classes
  inside unchanged jars.
- **Self-reference quirk:** `digest.txt` contains a `digest.txt = <hash>`
  line for itself, which looks paradoxical (a file can't meaningfully hash
  itself — a hash function including its own output as part of its input
  is a structurally circular fixed-point problem, not something a "good
  enough" algorithm resolves). Confirmed by the user: the listed hash is
  actually computed over every line in the file *except* the `digest.txt`
  line itself (and mind trailing-newline handling). This is the standard
  way to build a self-verifying manifest — the "hash of the hash-list"
  sits outside the thing it summarizes, similar in spirit to a Merkle
  root. **Implementation gotcha:** if the catalog tool ever wants to
  verify a fetched `digest.txt`/`digest2.txt` the same way Getdown's own
  client does (not just diff it), it must replicate this exact exclusion
  + newline handling or it'll get false mismatches.

## MAJOR CORRECTION: raw appbase access shows no evidence of the "5-version" retention limit

The "SK only keeps the 5 most recent versions accessible" claim (from the
Knight Launcher maintainer, via Discord — see "Why the patch-chain idea
doesn't transfer to Spiral Knights" below) was taken at face value and
drove a chunk of the legal/architecture analysis (urgency of capturing
promptly, conclusion that public diffs aren't meaningfully safer than
public jars). Actually tested it — the claim does not hold for raw appbase
file access, at least based on the evidence gathered so far.

**Test 1 — the `client/` channel's pinned version** (Feb 9, 2026,
`20260209004019`, already known from the two-channel investigation above):
`spiral/20260209004019/getdown.txt` and `.../digest.txt` both returned
**200 OK**, months after that version stopped being current.

**Test 2 — a genuinely orphaned old version, found with zero load on SK's
servers.** Queried the Internet Archive's Wayback Machine CDX index (not
SK's infrastructure at all) for anything ever crawled under
`gamemedia2.spiralknights.com/spiral/*`:
```
http://web.archive.org/cdx/search/cdx?url=gamemedia2.spiralknights.com/spiral/*&output=json&limit=100&collapse=urlkey
```
Found a version directory from **October 19, 2020** —
`spiral/20201019172329/` — six years old, and unlike the `client/` pin,
**nothing currently points at it at all**. Tested it directly:
```
http://gamemedia2.spiralknights.com/spiral/20201019172329/getdown.txt  → 200 OK
http://gamemedia2.spiralknights.com/spiral/20201019172329/digest.txt   → 200 OK
```
Fully live, full content, six years after the fact, with no current
pointer anywhere referencing it.

**Also found via the same CDX query, unrelated tangent worth noting:** a
`spiral_preview/client/` path (installer `.dmg`/`.exe`, dated July 2026) —
looks like a separate preview/beta distribution channel, not investigated
further yet.

**Revised understanding:**
- **Raw appbase directory/file access**: no observed pruning at all across
  two independent old-version tests, one 6+ years old with no current
  pointer. Looks like SK's origin may simply be a permanent, ever-growing
  archive that's never cleaned up — not the rolling 5-version window
  originally assumed.
- **The patch mechanism specifically**: still the only thing actually
  observed to 403 on an old target — but that test was cross-channel
  (`client/`'s Feb version → `latest/`'s current directory, see the
  cross-channel-vs-same-lineage distinction discussion below), so it
  doesn't cleanly confirm a distance/age limit either. Whether a
  same-lineage, large-gap patch request (e.g. `latest/` version N asking
  for a patch from `latest/` version N-20) also 403s is **still
  untested** — would need version numbers from further back in the
  `latest/` sequence than the one predecessor currently known
  (`20260807115345`), which will accumulate naturally once the catalog
  tool has been running for a while.
- Most likely explanation: the "5 versions" the maintainer described
  applies to the **patch mechanism**, not to raw per-version file access —
  though even that's not confirmed same-lineage yet, just plausible.

**Downstream sections that need re-examination in light of this** (not
rewritten here — flagging rather than silently editing, since this
changes load-bearing assumptions):
- "Why the patch-chain idea doesn't transfer to Spiral Knights" — built
  entirely on the retention claim not holding; if raw origin access is
  actually durable, the core argument (no permanent origin underneath a
  patch chain) may not apply, or may only apply to the patch data itself,
  not full jars.
- "Public vs. private artifacts — final split" — the private-artifact-
  registry decision was partly justified by "capture promptly or lose it
  forever." If SK's origin is durable, the safest architecture might be
  simpler than what was designed: consumers/tooling could resolve
  straight to SK's own origin per-version on demand, with much less need
  for a private mirror at all — worth revisiting rather than assuming the
  private-registry conclusion still holds as strongly.
- The "sync promptly, polling cadence must beat the retention window"
  urgency in the "Public vs. private artifacts" section — may be
  overstated if the underlying premise doesn't hold.

**Not yet re-decided** — this section documents the new evidence; the
actual architecture conclusions above have not been rewritten yet and
should be revisited explicitly rather than assumed to still stand as
originally reasoned.

### How far back does the durable archive actually go? (`gamemedia` vs `gamemedia2`)

Curiosity: since raw appbase access to old versions shows no pruning so
far, how far back can it actually go — far enough to predate obfuscation,
even? Investigated via the Wayback Machine CDX index (zero load on SK's
own servers) rather than guessing version numbers blindly.

**Two distinct CDN domains found, with overlapping but different
histories:**

- **`gamemedia2.spiralknights.com`** (the domain used throughout this
  whole investigation) — Wayback shows its `client/getdown.txt` already
  being crawled as early as **Aug 2019**, and one numbered version
  directory from **Oct 19, 2020** (`20201019172329`), confirmed still
  live (200 OK on `getdown.txt`/`digest.txt`) months/years after the
  fact, with nothing currently pointing at it. Wayback's crawl coverage
  of this domain is thin — the Oct 2020 hit is just the oldest numbered
  directory Wayback happened to crawl, **not necessarily the oldest one
  that actually exists on the server**. There could plausibly be older
  `gamemedia2` versions (e.g. from the 2019 migration window) still
  sitting on the server; no version number for one has been found yet.

- **`gamemedia.spiralknights.com`** (no "2") — a separate, older CDN
  domain. Wayback shows numbered version directories from **2014–2015**
  (`20140402151128`, `20140522134832`, `20140610174157`,
  `20140617171220`, `20140828175729`, `20141202165518`,
  `20141203124508`, `20150519171730`, `20150616160405`), plus a
  `client/getdown.txt` capture as late as **Oct 2018**. Also, oddly, a
  very recent Wayback hit (`spiral/client/background.png`, Feb 2026) —
  the domain is still alive today.

**Tested directly (HEAD requests only):**
```
http://gamemedia.spiralknights.com/spiral/client/getdown.txt              → 200 OK
http://gamemedia.spiralknights.com/spiral/20140402151128/getdown.txt      → 403
http://gamemedia.spiralknights.com/spiral/20140402151128/digest.txt       → 403
http://gamemedia2.spiralknights.com/spiral/20140402151128/getdown.txt     → 403
http://gamemedia2.spiralknights.com/spiral/20140402151128/digest.txt     → 403
```

**Fetched `gamemedia.spiralknights.com/spiral/client/getdown.txt`'s actual
content** — it's not independent archive content at all, it's a
pass-through alias:
```
version = 20260209004019
appbase = http://gamemedia2.spiralknights.com/spiral/%VERSION%
latest = http://gamemedia2.spiralknights.com/spiral/client/getdown.txt
```
Same frozen Feb 2026 snapshot as `gamemedia2`'s own `client/` channel —
`gamemedia` (no "2") exists today purely to catch old installed clients
still hardcoded to that hostname and hand them off to the real current
infrastructure, not as a real archive.

**Conclusions:**
- `gamemedia` (no "2") is **alive as a legacy redirect/compat shim, but
  dead as an archive** — its old numbered-version content (2014–2015) is
  gone (403), even though the hostname and its one `client/` alias path
  still respond.
- The 2014 version doesn't exist on `gamemedia2` either — confirmed 403
  there too. This isn't "content that moved and got pruned," it's "this
  version was never on `gamemedia2`'s storage to begin with" — the
  migration between the two domains most likely happened somewhere around
  2018–2019 (bounded loosely by the `client/getdown.txt` capture dates:
  `gamemedia`'s last capture Oct 2018, `gamemedia2`'s first capture Aug
  2019), and whatever existed only on the old domain before that point
  appears to not have been carried over.
- **Net result on the original curiosity** (finding an accidentally
  unobfuscated pre-2018 build): not reachable through either domain as
  currently tested — the 2014-era content is gone. The durable window
  confirmed so far remains bounded between roughly **Oct 2020 (confirmed
  alive) and the 2018–2019 migration (exact `gamemedia2` floor still
  unknown/untested)** — still a genuinely long window, just not back to
  the earliest years of the game as hoped.
- Not yet tried: hunting for a `gamemedia2` version number from the
  2019-ish migration window itself (old patch notes, forum posts, etc.)
  to pin the actual floor more precisely.

### Checked the Oct 2020 build for obfuscation — no fluke unobfuscated release found

Motivation: since raw appbase access to the Oct 2020 version works, and
the current build is confirmed ProGuard-obfuscated, curious whether an
accidentally-unobfuscated build might exist somewhere in that 6-year
window.

Downloaded the actual Oct 2020 `projectx-pcode.jar` (not just a HEAD
check) and saved it locally for reuse, rather than re-hitting SK's
servers again later:
```
historical_versions/20201019172329/getdown.txt
historical_versions/20201019172329/digest.txt
historical_versions/20201019172329/code/projectx-pcode.jar   (8,337,753 bytes)
```

**Result: obfuscation is present in the 2020 build, but it's selective,
not total** — e.g. in `com/threerings/projectx/admin/client/`, single-
letter obfuscated classes (`a.class`, `b.class`, `c.class`, ...) sit
alongside classes that kept their full original names (`ActorSpawner`,
`AdminDashboard`, `AdminTool`, `AnnouncementFieldEditor`, `BulkMailer`,
`ClientTimingsViewer`, `ConfigTool`), 8,197 total classes.

**Compared against the current (Aug 2026) build's already-decompiled
output** (`decompiled/projectx-pcode/`) — same package shows the exact
same pattern: `a.java`–`m.java` obfuscated, and the identical set of
class names (`ActorSpawner`, `AdminDashboard`, `AdminTool`,
`AnnouncementFieldEditor`, `BulkMailer`, `ClientTimingsViewer`,
`ConfigTool`, plus `EffectFirer`, `ItemCreator`, `LoadTester`,
`NodeCommander`) kept readable, six years later.

**Conclusion:** no accidentally-unobfuscated release found via this
check — obfuscation (and, notably, the *same specific selective pattern*
of which classes are exempted) looks stable across the entire ~6-year
span tested, not something that's drifted or had a gap. A real fluke, if
one exists, would more likely show up as a shift in *which* classes get
exempted (a misconfigured `-keep` list) than a simple all-or-nothing
toggle — and would need denser sampling across versions (not just the
two ends of the confirmed-durable range) to have a real chance of
catching, per the earlier discussion on why bookend-only sampling can't
detect anomalies.

## Current rights holder: Grey Havens

Web search (not exhaustively verified, but sourced) turned up that Spiral
Knights is now owned by **Grey Havens**, not Three Rings Design — per
[wiki.spiralknights.com/Three_Rings](https://wiki.spiralknights.com/Three_Rings)
and [wiki.spiralknights.com/Developer](https://wiki.spiralknights.com/Developer),
which also name Mark Johnson and Ray Greenwell as current engineers on the
team. Fills a gap the earlier legal-analysis section left as "whoever
currently owns Spiral Knights" — worth using "Grey Havens" specifically in
any future legal-context writing rather than defaulting to "Three Rings"
(Three Rings remains the correct attribution for the open-sourced
narya/nenya/vilya engine libraries specifically, which predate the
ownership change).

No developer blog post, postmortem, or documented build/CI process was
found for Three Rings/Grey Havens/Spiral Knights specifically — searched
for one while investigating whether pre-2020 releases might have had a
more manual (and therefore more error-prone) build pipeline, came up
empty. Any reasoning about pre-2020 CI maturity in this doc is general
industry-pattern speculation (GitHub Actions didn't exist before 2019;
Jenkins/Travis/Bamboo required real setup effort; smaller studios commonly
ran release scripts by hand well into the 2010s), not something confirmed
about this team specifically.

## Attempted to recover pre-2020 build content via Wayback's archived
   *content* (not just URL references) — dead end, but an interesting one

Motivation: since `gamemedia`'s old numbered version directories (2014–
2015) are 403 on the live server today, checked whether Wayback had
actually archived the real file *content* at crawl time (not just
recorded that the URL existed), which would recover historical build
artifacts independent of whether SK's live server still serves them.

Found one promising-looking CDX entry naming an actual file (not just a
bare directory): `gamemedia.spiralknights.com/spiral/20150616160405/getdown.txt`,
captured 2018-10-28. Tried to fetch the actual archived content via
Wayback's raw-playback URL forms (`.../<timestamp>if_/<url>` and
`.../<timestamp>id_/<url>`) — both came back **403**.

**Initially worth checking whether this meant Wayback itself was blocking
the requests** — verified via a direct CDX query scoped to that exact
URL, which returned the crawl's own recorded `statuscode: 403`. So this
isn't a live block or rate-limit at all — Wayback is faithfully replaying
a **real historical 403** that `gamemedia.spiralknights.com` itself
returned back on **Oct 28, 2018**, when Archive Team's "ArchiveBot"
crawler originally tried to fetch it. There's no archived content to
recover because there was nothing left to archive at capture time.

**Revises the earlier migration-timeline theory:** previously guessed the
`gamemedia`→`gamemedia2` migration happened around 2018–2019 based on
`client/getdown.txt` capture dates on each domain. This finding weakens
that: at least one individual old per-version directory on `gamemedia`
was *already* dead by Oct 2018 — years before any guessed migration date.
More likely explanation: individual old per-version directories on
`gamemedia` were being pruned/locked down on some ongoing basis
unrelated to (or well before) any domain-level migration, not dropped all
at once when `gamemedia2` took over.

**Side detail:** the capture was collected by **Archive Team's
"ArchiveBot"** — a volunteer crowdsourced crawler typically run as a
"panic download" when a site looks at risk of shutting down. Suggests
there was real community concern about Spiral Knights' continuity around
2018, though not investigated further.

## Recovered genuine 2015 client files from an old physical machine

Separate from the server/Wayback investigation above: the user has an old,
extremely slow Acer laptop (Windows 7) that turned out to still have a
Spiral Knights install on it from **2015** — potentially recoverable
through no other means, since both the live server and Wayback came up
empty for versions in that era (see the "already dead by 2018" and
"gamemedia2 doesn't have it either" findings above).

### Getting a usable terminal on very slow old hardware

Avoid anything that touches the modern Start menu UI (heavyweight on old
hardware) — use lightweight legacy paths instead: **Win+R → `cmd`** works
on any Windows version and bypasses Start entirely; **Win+X** (Win 8/10/11)
opens a lightweight legacy context menu; **Ctrl+Shift+Esc → Task Manager →
File → Run new task** if Explorer itself is unresponsive; **Safe Mode with
Command Prompt** (F8 at boot on Win7, Shift+Restart → Troubleshoot →
Advanced Options → Startup Settings on Win8+) if the whole GUI is the
bottleneck, since it skips loading Explorer/most drivers/startup programs
entirely.

### Finding the install

Windows 7 uses the same `AppData` structure as modern Windows (introduced
in Vista) — same paths as the current machine apply. Multiple install
conventions exist depending on how the game was installed, confirmed via
forum/wiki search:
- Standalone: `AppData\Local\Spiral Knights\app\` (matches current
  machine)
- Also standalone, alternate convention: `AppData\Local\Three Rings
  Design\Spiral Knights\`
- Web launcher: `AppData\LocalLow\spiral\`
- Older local client, per forum post: `AppData\Roaming\Three Rings
  Design\Spiral Knights\` (the actual find here had only empty `ui`/
  `animation` subfolders in Roaming — likely just cached UI resource
  extracts, not the real install; the jars were found under `spiral/`
  elsewhere on the drive via full search)
- Steam version: `Program Files (x86)\Steam\steamapps\common\spiral
  knights\`

Full-drive fallback search, redirected to a file so progress can be
checked from a second window without disturbing the (slow, disk-I/O-bound)
scan:
```
dir /s /b C:\ | findstr /i "projectx-pcode.jar" > C:\found.txt
```
Note: `dir | findstr` piped output is buffered, not necessarily flushed
per-line, so absence of output in-progress doesn't mean "not found yet"
vs. "found but not flushed" — redirecting to a file and `type`-ing it from
a second window sidesteps the ambiguity. Ctrl+C cleanly interrupts a
running foreground pipeline if needed (signals the whole pipeline, not
just one process).

### Transferring the files over the LAN

Goal: move files **from** the old Acer **to** the main machine, both on
the same network. Corrected course a couple of times before landing on
the right approach:

- **HTTP GET pulling from a server on the main machine** — wrong
  direction; GET only pulls, so this would move files the other way
  (main machine → Acer), not what was needed.
- **SMB share/`copy`** — viable native option (Windows 7 supports SMB2,
  so no legacy SMBv1 concerns talking to a modern host), considered as
  fallback but not what was ultimately used.
- **HTTP PUT with the file as the request body, initiated from the
  Acer** — this is what worked. Two ways to do this without installing
  new software on the slow machine:
  - **PowerShell (present since Windows 7, PS 2.0)**: `System.Net.WebClient.UploadFile(url, method, path)`
    supports raw PUT uploads directly, no multipart wrapping needed.
    ```
    powershell -Command "(New-Object Net.WebClient).UploadFile('http://<main-pc-ip>:8000/projectx-pcode.jar', 'PUT', 'projectx-pcode.jar')"
    ```
    The file-path argument resolves relative to the invoking shell's
    current directory, so a bare relative path works fine as long as
    you're already `cd`'d into the right folder.
  - **`cscript.exe` + `WinHttp.WinHttpRequest`/`ADODB.Stream` COM
    objects** — genuinely native alternative with less startup overhead
    than PowerShell on old hardware, no GUI/editor needed to create the
    script (build it purely from `cmd` via chained `echo ... >>
    file.vbs` commands, which is more paste-reliable than the classic
    `copy con` trick since each line is a self-contained command). Not
    ultimately needed since the PowerShell route worked, but documented
    here as the lighter-weight fallback if PowerShell's startup cost
    ever becomes the bottleneck.
- **Receiving server**: a small Python script
  (`historical_versions/upload_server.py` in this repo) using
  `http.server.BaseHTTPRequestHandler` with a `do_PUT` override that
  writes the request body straight to disk under
  `historical_versions/from_acer/`. Verified working end-to-end with a
  loopback (`localhost`) test before trying cross-machine, which is a
  good pattern to isolate "is it the server" vs. "is it the network path"
  when debugging a transfer failure.
- First real cross-machine attempt threw a generic PowerShell
  `MethodInvocationException` — cause was simply that the receiving
  server hadn't been started yet (connection refused, surfaced with
  minimal detail by PS 2.0's error reporting). Lesson: when this error
  shows up, check "is anything actually listening on the target port"
  before assuming something more exotic — `Get-NetTCPConnection
  -LocalPort <port>` confirms it from the receiving end.
- The transfer itself is read-only on the source — `WebClient.UploadFile`
  only reads the source file and streams it out, never modifies/deletes
  the original.

### What was actually recovered

`historical_versions/from_acer/` now contains real files pulled directly
off the old machine:
- `getdown.txt` — reveals the actual version: **`20151118125413`**
  (Nov 18, 2015). Matches the 2014–2015 Wayback captures for the old
  `gamemedia` (no "2") domain exactly.
- `projectx-pcode.jar` (8,199,528 bytes, 8,197 entries, valid/uncorrupted
  zip, confirmed via loopback-tested transfer pipeline).

**Notable details from this `getdown.txt`:**
- `# $Id: getdown.txt 19918 2005-03-23 20:44:52Z mdb $` — an old SVN
  keyword-expansion header. The config template traces back to a source
  file from **March 2005**, authored by "mdb" — almost certainly
  **Michael Bayne**, Three Rings' co-founder and the original author of
  Getdown itself. So this one line traces straight back to the tool's
  creator, a decade before this particular deployment.
- `java_version = 1060000` (Java 6) — vs. the modern build's bundled
  Java 25; `appbase`/`latest` point at the old `gamemedia` domain, not
  `gamemedia2`; classpath uses the same LWJGL2-era naming
  (`lwjgl.jar`, `lwjgl_util.jar`, `jinput.jar`, etc.) as the Oct 2020
  build, consistent lineage pre-dating the LWJGL3/Steam/64-bit overhaul.
- `tracking_url` uses old Google Analytics Urchin tracking
  (`__utm.gif`, `utmwv=4.5.9`) — long-deprecated GA mechanism, another
  period-accurate detail.

**Significance:** this version may not be recoverable through any other
currently-known means — the live server's old `gamemedia` numbered
directories are confirmed dead (403), and Wayback's own archived capture
of a nearby 2015 URL turned out to be a stored 403 too (see "already dead
by 2018" above), meaning there's no server-side or archive-side path back
to this era that's been found so far. This physical machine may be the
only surviving copy accessible short of finding another old install
somewhere.

### Obfuscation check on the 2015 build — same result as 2020/2026, no fluke

Checked `com/threerings/projectx/admin/client/` the same way as before:
**identical selective-obfuscation pattern** — `a.class` through `f.class`
obfuscated, `ActorSpawner`, `AdminDashboard`, `AdminTool`,
`AnnouncementFieldEditor`, `BulkMailer`, `ClientTimingsViewer` kept fully
readable — the exact same set of exempted classes as the 2020 and 2026
builds. Extends the "obfuscation setup has been stable" finding from a
6-year span to an **11-year span** (2015 → 2026). Still no accidentally-
unobfuscated release found, but a much stronger confirmation of pipeline
stability than the 2020 sample alone provided.

## Getdown's incremental patch mechanism (much cheaper than full re-fetch)

Tipped off via Discord that standalone Getdown updates use incremental
patch files, not just full-file re-fetches. Confirmed by inspecting the
local `launcher.log` (`app/launcher.log`) after a real update, and by
reading Getdown's own source
([`Patcher.java`](https://github.com/threerings/getdown/blob/master/core/src/main/java/com/threerings/getdown/tools/Patcher.java)).

**Observed in `launcher.log` from the last real update:**
```
http://gamemedia2.spiralknights.com/spiral/20260812101850/patch20260807115345.dat       (4.97 MB)
http://gamemedia2.spiralknights.com/spiral/20260812101850/patch-full20260807115345.dat  (22 bytes)
```
- **Naming convention:** `patch<FROM_VERSION>.dat`, hosted at
  `<appbase-of-TO_VERSION>/patch<FROM_VERSION>.dat`. The tiny
  `patch-full<...>.dat` alongside it is presumably a small marker/flag
  file, not real patch content — not yet inspected directly.
- **Files actually touched by that one patch:** `code/config.jar`,
  `code/projectx-config.jar`, `code/projectx-pcode.jar`,
  `rsrc/intro-bundle.jar`, `crucible.jar` — 5 files, vs. the full ~15-file
  classpath. Real bandwidth win if `sync` can use patches between
  consecutive versions it already has cached, instead of always re-fetching
  everything.
- **Real version-cadence data point:** predecessor version was
  `20260807115345` (Aug 7) → current `20260812101850` (Aug 12) — a
  **5-day gap**. Useful for sizing the polling interval against the
  5-version retention window (needs to comfortably beat this cadence).
- **`crucible.jar` isn't in `getdown.txt`'s `code =` list at all** — it's
  loaded separately via the `-Dcrucible.dir=../crucible` JVM arg seen in
  the launch command, but Getdown still tracks/patches it. Worth including
  in whatever the catalog tracks, not just the primary classpath entries.

**Patch file format, confirmed from Getdown's own source:** one unified
patch file per version transition (not one per changed file). Internally
it's a **ZIP archive with a suffix-based entry convention**:
- `foo.create` — brand-new file, stored whole (nothing to diff against).
- `foo.patch` — file existed in both versions and changed — actual delta.
- `foo.delete` — marker entry, no content, signals the file should be
  removed.

Getdown walks the zip and applies create/patch/delete per entry to update
the install directory — matches the Discord description exactly (new/
edited files + a removal manifest, bundled together).

**The `.patch` entries are handled by `JarDiffPatcher` — jar-aware, not a
raw byte-level binary diff.** Since jars are themselves zip files, byte-
diffing a compressed jar as one opaque blob would produce huge, useless
deltas (compression scrambles byte alignment even for tiny source changes).
Instead `JarDiffPatcher` looks *inside* the jar at the individual
class-file entries and only stores deltas/replacements for the class files
that actually changed, leaving unchanged entries alone — same concept as
the old Java Web Start "JarDiff" format from the JNLP spec.

**Implication for the catalog's diffing goal:** if `JarDiffPatcher` already
operates at individual-`.class`-inside-the-jar granularity, the patch
archive itself may already reveal exactly which classes changed inside
e.g. `projectx-pcode.jar` between two consecutive versions — potentially
the exact "what changed" signal the class-manifest-diffing idea wanted,
available for free by inspecting the patch's internal structure, rather
than needing to unzip/hash both jars yourself. Not yet verified by directly
inspecting a `.patch` entry's actual bytes — worth doing before relying on
it.

**Caveat:** patch files are almost certainly only generated/served for
specific N→N+1 transitions (or a small window of recent transitions), tying
back into the 5-version retention theme — probably not available for
arbitrary version-to-version jumps, only sequential steps, and possibly
only briefly. Worth confirming how far back patches remain fetchable.

### Hands-on inspection of a real patch file

Fetched `patch20260807115345.dat` directly (plain HTTP GET — see "How the
patch file was actually fetched" below) and inspected it:

- Top-level zip, 5 entries, all `.patch` suffix (no `.create`/`.delete` in
  this particular transition): `code/config.jar.patch`,
  `code/projectx-config.jar.patch`, `code/projectx-pcode.jar.patch`,
  `rsrc/intro-bundle.jar.patch`, `crucible.jar.patch`.
- Each `.patch` entry is itself a **nested ZIP** (`PK\x03\x04` magic)
  containing `META-INF/INDEX.JD` (`JD` = JarDiff, the old Java Web Start
  JarDiff spec format) plus whichever inner files changed. `INDEX.JD`
  content is minimal (`version 1.0`) — no manifest listing, since presence/
  absence of an entry in the archive *is* the diff signal.
- **`code/config.jar.patch`**: only inner file present is
  `build.properties`, containing:
  ```
  #Wed, 12 Aug 2026 10:18:50 -0700
  version=20260812101850
  config_version=20260812101850
  ```
  — SK's own build/version stamp, plain text.
- **`code/projectx-pcode.jar.patch`**: 2,022 `.class` entries + 4
  `.properties`, out of 8,269 total classes in the full jar (~24%). No
  `.gdiff`-suffixed entries anywhere and no `.delete` entries — confirms
  this JarDiffPatcher implementation does **whole-file replacement** for
  any entry that differs at all (not true sub-file binary deltas); presence
  in the archive alone signals "changed."

**Tested whether "changed" bytecode means "changed logic" — it often
doesn't.** Picked one 801-byte changed class,
`com/threerings/projectx/guild/client/I.class`, fetched the corresponding
old version directly (see below — the Aug 7 version was still live at its
own `appbase`, at least at time of testing), and diffed `javap -c -p -v`
disassembly of old vs. new. The **entire** diff:
```
- static final int[] bqs;
+ static final int[] bqt;
```
One static field renamed (`bqs` → `bqt`) — every instruction and every
other constant byte-for-byte identical. Confirms (not just hypothesizes)
that a real chunk of the ~24% "changed" class figure is pure
obfuscation-renaming churn (a global rename elsewhere cascading into this
class's constant pool) with zero semantic/behavioral difference, not
actual logic changes.

**Implication:** raw hash/byte-diffing (what `digest.txt` or the patch
file give for free) conflates "this class's logic changed" with "some
symbol it references got renamed elsewhere." A genuinely useful changelog
signal needs identifier-normalization before comparing — the same kind of
structural-fingerprinting approach as the known-library-substitution idea
above, applied here to separate real logic changes from renaming noise.

### Patch URL addressing scheme — confirmed as plain, unauthenticated GET

Worth spelling out explicitly since it's easy to assume there's more
negotiation involved than there actually is:

```
http://gamemedia2.spiralknights.com/spiral/<NEWER_VERSION>/patch<OLDER_VERSION>.dat
                                            ^^^^^^^^^^^^^^^                          appbase directory of the
                                                                                      version being updated TO
                                                             patch<OLDER_VERSION>.dat  literal filename inside it,
                                                                                       naming the version being
                                                                                       updated FROM
```

`spiral/<version>/` is just that version's ordinary `appbase` directory —
same one that serves `digest.txt`, `code/*.jar`, etc. There's nothing
special about how the patch file is addressed: no query string, no auth
header, no session/negotiation step. This isn't a guessed/reverse-
engineered scheme either — it's the literal URL the real Spiral Knights
client requested, as captured directly from `launcher.log`.

Practical consequence for `sync`: given the last version already held
locally and the new current version from `latest/getdown.txt` (or
`client/getdown.txt` for that channel), the tool can just attempt
`GET <new-appbase>/patch<old-version>.dat` directly — no discovery step
needed beyond knowing both version numbers.

### How the patch file was actually fetched

No special API — plain HTTP GET against the URL pattern already documented
above (`<appbase-of-TO_VERSION>/patch<FROM_VERSION>.dat`), same as every
other Getdown-served file. Note for tooling: the sandboxed Bash tool's
`curl` failed here with a DNS resolution error (`getaddrinfo() thread
failed to start`) — unrelated to Spiral Knights, just an environment quirk
— switching to PowerShell's `Invoke-WebRequest` worked fine. Worth keeping
in mind if the real `sync` implementation ends up needing a fallback HTTP
client depending on execution environment.

## Third-party/vendored code inside proprietary jars

Confirmed via investigation: Three Rings open-sourced their engine stack
(narya/nenya/vilya, LGPL-2.1, still on [github.com/threerings](https://github.com/threerings)),
and `projectx-pcode.jar` bundles a vendored copy of the `samskivert` utility
library (a perfect match against known upstream) — but the whole jar,
including the vendored samskivert classes, got run through an obfuscator.
This means `pcode.jar` is a shaded/uber jar: SK's own proprietary code and
vendored open-source code merged into one artifact, then obfuscated in a
single blanket pass (why even the OSS classes are obfuscated — the build
just didn't carve out exceptions for vendored code before minifying).

Implication for the public/private split: can't split proprietary vs. not
at jar granularity for `pcode.jar` — has to happen at the class/package
level, since genuinely proprietary and merely-vendored-OSS classes live in
the same file.

**Forward-looking idea for the decompile plugin:** recognize known vendored
libraries (samskivert, narya, nenya, vilya, etc.) inside an obfuscated jar
and substitute the real upstream source instead of running them through a
decompiler — better quality than machine-decompiled output, and cleanly
separates "we just re-derived known OSS" from "this is actual novel SK
logic" in whatever gets tracked/diffed. Matching approach, since classes
are obfuscated (can't match by name):
- **String/constant-literal matching** as the primary signal — obfuscators
  generally leave string constants alone (they'd change runtime behavior
  otherwise), so exact string-constant overlap against a known class is a
  reliable tell.
- **Structural fingerprinting** as a corroborating signal — method/field
  counts, descriptor shapes (survive rename-only obfuscation since they're
  JVM type descriptors, not identifiers), normalized-identifier bytecode
  hashing, compared against a small corpus of known library releases
  (checking multiple historical versions, not just HEAD, since the vendored
  copy may be from an older release).
- Once matched, could also tag classes in the catalog's class-manifest
  metadata as `known-library:samskivert@vX` vs `unmatched`, so diffs
  naturally separate "recompiled known OSS" from "novel SK logic" — likely
  a large fraction of `pcode.jar` given it's a shaded jar.
- Caveat: this only works cleanly against rename-only obfuscation (which
  is what SK appears to use, given the "perfect match"). More aggressive
  obfuscation (control-flow flattening, string encryption) would make
  fingerprint matching much noisier and require treating matches
  probabilistically rather than assuming exact hits.

## Consumer side

A thin Gradle plugin (or just docs, TBD) for other repos to depend on
published artifacts: adds the (private) GitHub Packages Maven repo +
PAT-based credentials for an authorized account, so a consuming project can
write something like:

```kotlin
implementation("com.spiralknights.client:projectx-pcode:20260812101850")
```

with sources attached for IDE navigation, once the catalog tool has
published that version. The plugin itself is public/open-source; the repo
it points at is private.

### Alternative Maven hosting options (if GitHub Packages' free limits bind)

GitHub Packages free tier: 500 MB storage / 1 GB transfer per month
(personal Free plan) — storage is not a near-term concern given jar sizes
below, but the 1 GB/month transfer cap is the more likely thing to bind if
more than a couple people start pulling regularly. Surveyed alternatives:

- **GitLab Package Registry** — 5 GB free per namespace, but shared across
  *all* storage types (repo + LFS + packages + container registry), not
  packages-only. Same static-PAT auth model as GitHub, real Maven support.
  Closest lateral move if GitHub's cap ever binds.
- **AWS CodeArtifact** — 2 GB storage + 100k requests/month, ongoing free
  tier (not a 12-month trial), $0.05/GB-month + $0.05/10k requests past
  that. Native Maven support, but auth is IAM-based short-lived tokens
  (~12h) rather than a static PAT — consumers need the AWS CLI configured
  and to periodically re-run `aws codeartifact login`, more setup friction
  than GitHub/GitLab for the same outcome.
- **Self-hosted (Reposilite or Nexus Repository OSS)** — free, open-source,
  no vendor storage cap at all (bounded only by your own disk). Simple
  HTTP Basic Auth is easy for consumers, but you own hosting/uptime/TLS —
  friction shifts from consumer to operator.
- **Object storage as a flat repo (Cloudflare R2 / Backblaze B2)** — same
  trick as the earlier GitHub-Pages idea, but access-gated instead of
  public. R2: 10 GB free storage, **zero egress fees** (its main selling
  point vs. S3) — makes the "monthly transfer cap" problem disappear
  entirely. Tradeoff: plain object storage doesn't speak Maven's auth
  handshake, so you'd need to build a thin auth bridge yourself (e.g. a
  Cloudflare Worker translating Basic Auth into signed bucket access)
  before Gradle can talk to it at all — most setup work of the options.

**Size reality check:** `compiled_jars/` (full classpath incl. LWJGL/
Commons/etc.) is ~15 MB per version; the genuinely proprietary subset
(`projectx-pcode.jar` + `projectx-config.jar` + `config.jar`, pending the
`config.jar` provenance check above) is ~10.3 MB per version. At that size,
500 MB storage covers ~30+ versions; 1 GB/month transfer covers roughly
~66-100 full pulls/month before overage — fine for personal/small-group use,
worth revisiting if usage grows.

**Decision: stick with GitHub Packages for now** — friction found so far
(transfer cap) isn't currently binding given actual jar sizes, and it's a
lateral/free move to GitLab later if it ever does bind. No self-hosting or
object-storage complexity taken on unless actually needed.

### Consumer-side plugin auth: automatic token refresh

Gradle's credential resolution isn't limited to static values — a
repository's `credentials { }` block can run arbitrary code to produce
username/password at resolution time, and there's a formal
`Provider<PasswordCredentials>` API (since ~Gradle 6.6) for doing this in a
configuration-cache-friendly way. So the consumer plugin can do better than
"paste a PAT and forget it" if desired:

- **Option A (recommended starting point, minimal work):** shell out to
  `gh auth token` at credential-resolution time, piggybacking on a session
  the user already has via `gh auth login`. No OAuth implementation needed
  in the plugin at all.
- **Option B (more work, real silent refresh):** register a GitHub OAuth/
  GitHub App with expiring user tokens (on by default for new apps) —
  8-hour access token + 6-month refresh token
  ([docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens)).
  Do a one-time interactive Device Flow login (no client secret required),
  stash the access+refresh tokens locally (OS keychain ideally), and have
  the plugin's credentials provider silently refresh the access token via
  the refresh token before each build once it's past 8h old. Real "log in
  once, keeps working for up to 6 months" behavior, independent of whether
  `gh` happens to be installed.

Doesn't matter for CI either way — `GITHUB_TOKEN` is already minted fresh
per workflow run. This is purely a local-dev-experience nicety, not a
blocker for the initial design; Option A gets most of the benefit cheaply,
Option B can come later if `gh`-CLI-required turns out to be a bad
assumption about the audience.

## Open / not yet decided

- Naming/repo layout: is `sk-client-catalog` this repo evolved, or a new
  repo? Where does the decompile-side tool live?
- Exact Maven coordinate scheme (groupId/artifactId per jar).
- Whether/how to prune old versions from the (now private) Packages repo
  over time.
- Class-manifest diffing format specifics (just class list vs per-class
  hash vs something richer).
- Gradle plugin details for the consumer side.
- How often Spiral Knights actually ships versions, to size the polling
  interval against the 5-version retention window.
- Who, if anyone, gets added as a collaborator to the private artifact
  repo beyond personal use.
- Not yet started: any actual implementation.
