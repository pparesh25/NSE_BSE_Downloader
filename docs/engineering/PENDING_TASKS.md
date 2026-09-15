# Pending engineering tasks

Last reviewed: 2026-08-20 (Asia/Kolkata)
Repository branch at review: `main`

## Working convention

All new or modified repository documentation, source-code comments, and Git commit
subjects and bodies must be written in English. Historical documents do not require
a language-only rewrite unless they are being substantively revised. Localized
user-facing application text is outside this engineering-record convention.

This file is the central index for approved but incomplete engineering work. Detailed
research and evidence files remain authoritative references, but every deferred topic
must also be recorded here so it is not lost between phases.

## 0. v1.1 release publication — 1.1.0 published, withdrawn, republished as 1.1.1

Status: **Superseded by v1.1.1.** Paused on 2026-08-06 so that code-level defect
remediation could go first; resumed once Phase 3 of
[CODE_DEFECT_REMEDIATION_PLAN.md](CODE_DEFECT_REMEDIATION_PLAN.md) closed, which
removed every defect the pause existed to avoid shipping.

Published from tag `v1.1.0` at commit `059f497`, then unpublished on 2026-08-14 with
all six assets: the shipped builds carried no certificate store and could not download
anything. The defect and its reproduction are in
[RELEASE_BUILD_EVIDENCE.md](RELEASE_BUILD_EVIDENCE.md); the work to fix it is §3 below.
The `v1.1.0` tag remains, so
<https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.1.0> still resolves to
the tag and its source archives. `main` still reads `1.1.0`, so installed v1.0.1
clients still announce the update; `README.md` and `UPGRADE.md` were corrected to send
them to the source install rather than to an empty releases page, and restored when
v1.1.1 was published. Release notes: [RELEASE_NOTES_1.1.1.md](RELEASE_NOTES_1.1.1.md).

The withdrawal cost one release cycle and no user data: the broken builds could not
write anything, because they could not download anything.

The steps below are kept as the record of how it was done, and as the template for the
next release.

Detailed record: [RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md),
[RELEASE_IMPLEMENTATION_PLAN.md](RELEASE_IMPLEMENTATION_PLAN.md)

### Already closed

Branch protection on `main` with `enforce_admins` (verified by a rejected push);
per-platform `__update_artifacts__` schema; release builds tied to `v*.*.*` tags
instead of every commit; notification-only decision; unsigned-release decision;
repository housekeeping; and a full three-platform rehearsal build (run 31054912467,
all green, checksums verified, packaged macOS app launched).

### Remaining, in order

1. Set the real release date in `version.py` — `__build_date__` and the
   `VERSION_HISTORY` `release_date`. Do not touch `__version__`, the history key, or
   the shape of that dictionary; installed v1.0.1 clients parse all three by regular
   expression.
2. Tag `v1.1.0` and let the tag push build the artifacts.
3. Download every asset and verify each `.zip.sha256` with `shasum -a 256 -c`.
4. Create the GitHub Release with all six files, using
   [RELEASE_NOTES_1.1.0.md](RELEASE_NOTES_1.1.0.md).
5. Rebuild `RELEASE_BUILD_EVIDENCE.md` at the tagged commit. It is still pinned to
   `9dba2ac`, which is superseded; an integrity record that does not describe the
   shipped artifact is worse than none.
6. Download from the published Release page and run on a clean machine.
7. Merge PR #10 with a merge commit. **This is the point of no return** — the moment
   `main/version.py` reads `1.1.0`, every running v1.0.1 client announces it within
   three seconds of its next launch.

### How it actually went

All seven steps completed on 2026-08-09, in that order. Two things worth carrying to
the next release:

- **`gh release create` with 198 MB of assets did not finish inside ten minutes.**
  `gh` creates the release as a *draft*, uploads, then publishes, so the interruption
  left a draft with only the three small sidecars and nothing public — a good failure
  mode. The zips were uploaded to that draft separately and the draft was published
  once all six assets were present. Upload the large assets in their own step.
- **The release notes linked to `blob/main/UPGRADE.md`, which did not exist on `main`
  until step 7.** Anyone opening the Release page between steps 4 and 7 would have hit
  a 404. The links now point at `blob/v1.1.0/`, which is also the correct habit: a
  release should describe fixed content, not a branch that moves after publication.

### Known gap in the tooling

Neither workflow publishes a GitHub Release; both end at `actions/upload-artifact`
with 14-day retention. Steps 3–4 were therefore manual. Worth automating before the
second release.

### Why it was paused

The release would have shipped the defects catalogued in
[CODE_DEFECT_REMEDIATION_PLAN.md](CODE_DEFECT_REMEDIATION_PLAN.md), two of which
blocked the owner's own stated goal of building a multi-year daily and symbol-wise
database. Publishing first would have meant notifying every existing user to upgrade
to a build that could not complete a historical backfill. Phases 1–3 closed those
defects first, which is what made this release publishable.

## 1. BSE SME/Startup classification and symbol naming — closed 2026-08-20, no change

Status: **Closed. The owner decided on 2026-08-20 to leave the current behaviour
exactly as it is.** The research below is preserved because it is correct and would be
the starting point if this is ever reopened, but no implementation is approved and none
is pending.

Detailed record:
[BSE_SME_STARTUP_SUFFIX_FINDINGS.md](BSE_SME_STARTUP_SUFFIX_FINDINGS.md)

### What "no change" means, stated precisely

- `BSE_EQUITY_SERIES` (`src/services/canonical_data.py:90-92`) stays
  `{A, B, M, MS, MT, P, R, T, W, X, XT, Z, ZP}`. **`TS` is deliberately not added**, so
  BSE Startup `TS` scrips remain absent from published BSE Equity output in every
  source era. That is an accepted omission, not an open defect.
- No suffix is applied to any BSE group. `M`, `MT` and `MS` keep their plain symbols,
  and no `_SME` or `_STARTUP` naming is introduced.
- No separate SME/Startup selector or separate output is added. BSE Equity remains one
  download and one segment.
- No migration of already published files, because nothing about their content changes.

### Why this is a reasonable place to stop

- The measured cost is small: sampling the 2026-08-07 BSE bhavcopy found **one** `TS`
  scrip dropped. The owner judged that low-stakes against the work and the migration
  risk of changing published naming.
- The omission is inherited from v1.0.1, so no existing user's data becomes wrong; it
  simply stays as complete as it has always been.
- Leaving it alone keeps published symbol files stable. Adding `TS` or a suffix later
  is the change that would force an audited migration of files already on disk — which
  is precisely why it should not be done casually mid-backfill.

### What this unblocks

This item no longer gates anything. In particular it does **not** gate the large BSE
backfill, and it does not gate Phase 5 of
[CODE_DEFECT_REMEDIATION_PLAN.md](CODE_DEFECT_REMEDIATION_PLAN.md).

### Preserved findings

- The single BSE cash-market bhavcopy already carries regular SME and Startup rows;
  no separate network download is required.
- `M` and `MT` are regular BSE SME groups. `MS` and `TS` are the Startup
  sub-segment under the broader BSE SME platform.
- Legacy reports expose the classification through `SC_GROUP`; current UDiFF reports
  expose it through `SctySrs`. Both must map to canonical `SERIES` before business
  classification.
- The reference `Mark Python` project appends `_SME` only for `M` and `MT`. This is
  an application naming convention, not an official BSE ticker format.
- The current downloader accepts `M`, `MT`, and `MS` without applying a suffix. `TS`
  is missing from the accepted group set and is therefore dropped.
- Delivery matching uses BSE security code, so display-name suffixing must remain
  separate from delivery identity.
- Price bands and old settlement-cycle wording must not be used as classification
  keys. Group/series is the authority.

### If this is ever reopened

Three product decisions would have to be answered first — the public symbol policy for
`MS`/`TS`, whether a separate SME/Startup selector is wanted, and whether existing
unsuffixed history is migrated or left historical. The implementation direction and the
evidence bar that were drafted for this work are in
[BSE_SME_STARTUP_SUFFIX_FINDINGS.md](BSE_SME_STARTUP_SUFFIX_FINDINGS.md). Reopening
after a multi-year backfill is materially more expensive than reopening now, because
the migration would then span years of published files rather than weeks.

### Separate scope

BSE FO remains a separate research and implementation task. It was never part of the
cash-market SME/Startup naming work and is unaffected by this decision.

## 2. Deferred pandas-to-Polars evaluation

Status: No production migration now; isolated proof of concept deferred.

Supporting evidence:
[PHASE_7_6_STAGED_OPTIMIZATION_REPORT.md](PHASE_7_6_STAGED_OPTIMIZATION_REPORT.md)

### Recorded decision and rationale

- Do not migrate the production application from pandas to Polars now.
- Do not add Polars or PyArrow to production requirements before an isolated proof of
  concept passes every acceptance gate below.
- Phase 7 profiling measured approximately 7,426 individual history-file writes, a
  10.936-second median history batch, and a 14.410-second staged median run. The main
  live Select-All cost is symbol-history finalization and disk publication, not
  DataFrame computation.
- Polars can improve CPU-bound parsing, filtering, sorting, joins, concatenation, and
  some large-workload memory use, but it does not remove network waits or thousands
  of individual filesystem publications.
- A full migration currently spans fourteen production modules and risks behavioral
  differences in data types, nulls, dates, stable ordering, duplicate resolution, and
  CSV serialization.
- Revisit this decision only through the defined experiment, not through another
  general pandas-versus-Polars architecture discussion.

### Isolated proof-of-concept scope

- Use a dedicated branch, isolated environment, and temporary data root. Never write
  to `/Users/paresh/NSE_BSE_Data`.
- Install Polars only in the isolated environment initially; leave production
  dependency files unchanged.
- Prototype only report parsing, canonical normalization, delivery joins, and
  in-memory combined-frame preparation.
- Keep symbol-history persistence, transactional state, corporate-action semantics,
  and final public CSV serialization on the current implementation during the first
  experiment.
- Do not combine the experiment with BSE SME/Startup naming, BSE FO, scheduling,
  state storage, GUI, or packaging changes.
- Benchmark representative 1, 20, and 101-day single-segment and Select-All runs.
- Measure target-stage and end-to-end wall time, peak resident memory, event-loop lag,
  and pandas/Polars boundary-conversion overhead.
- Verify exact public-output SHA, row count, column order, row order, null handling,
  date and numeric formatting, duplicate resolution, and error behavior.
- Run the full suite in both supported environments with isolated temporary data
  roots.
- Before production adoption, validate startup time, dependency footprint, and actual
  Nuitka packaging on every supported target platform.

### Acceptance gates

All correctness and packaging gates must pass. In addition, the experiment must show
at least one material performance benefit:

- at least 15-20% lower end-to-end Select-All wall time; or
- at least 35-40% lower time in the targeted parsing, normalization, and join stages
  without a material boundary-conversion penalty.

The experiment should also target at least 25% lower peak memory for the 101-day
workload. Memory improvement alone is not sufficient while the current workload
remains safely bounded.

If the gates fail, publish the evidence, remove the experimental dependency, and
retain pandas. If they pass, prepare a separate migration plan with an explicit
rollback strategy; do not proceed directly from the benchmark to a full rewrite.

### Higher-priority performance work

Until new profiling evidence changes the bottleneck, prioritize safely skipping
unchanged symbol-history files, reducing unnecessary publication, and evaluating
bounded file-write improvements. The current pipeline already batches history merges
in memory, but it must still publish thousands of individual files for a fresh
Select-All run.

## 3. Packaged builds have no certificate store — done 2026-08-18

Status: **Done.** Fixed, merged and proved on compiled artifacts for all three
platforms before v1.1.1 was published.

Detailed record: [RELEASE_BUILD_EVIDENCE.md](RELEASE_BUILD_EVIDENCE.md), "Why the
release was withdrawn".

### What is wrong

The Nuitka bundle ships its own OpenSSL, compiled with an `OPENSSLDIR` that exists
only on the build runner, and no CA bundle is included. The packaged application
therefore starts with zero trusted roots and every HTTPS request fails certificate
verification. `certifi` is not a dependency at all, and `http_client.py` passes
`ssl=True`, which relies on whatever OpenSSL's compiled-in default happens to be.

### Approved implementation direction

- Add `certifi` to `requirements.txt` as a runtime dependency.
- Build the TLS context from `certifi.where()` in one place, and use it for both the
  downloader and the update checker, rather than relying on OpenSSL defaults.
- Ensure the packaging step includes `certifi`'s data file in the bundle, and assert
  its presence in the packaging dry run rather than discovering its absence at runtime.
- Prefer an explicit, verifiable trust store over an `SSL_CERT_FILE` environment
  variable set at startup: the environment variable works, but it is invisible to the
  test suite and to anyone reading the code.

### Evidence produced

- `tests/test_tls_trust_store.py` asserts that a bundle travels with the application
  and that all three connection sites use it, so a future packaging change cannot
  silently drop them again.
- **A compiled artifact on each platform completed a real HTTPS request**, in
  `workflow_dispatch` run 31821982454. Each resolved its bundle from *inside* the
  artifact, not from the host: `…/main.app/Contents/MacOS/certifi/cacert.pem`,
  `/tmp/onefile_…/certifi/cacert.pem`, `…\Temp\onefile_…\certifi\cacert.pem`,
  121 authorities each. That is the property no source-level test can establish.
- A/B on the owner's Mac, same endpoint, same minute: the withdrawn build still fails
  with `CERTIFICATE_VERIFY_FAILED`; the fixed build reaches the HTTP layer.
- Full gate set re-run in both supported environments.

### What it changed about how a release is verified

`--verify-tls` now runs on the compiled artifact during packaging, and prints which
bundle travelled and how many authorities loaded. Running the packaged artifact on a
real machine also found a defect in that check itself: it read an HTTP 429 as a
certificate failure, which would have failed release builds at random. A status line
proves the handshake completed, so it now passes, and an unreachable endpoint is
reported as inconclusive rather than as a certificate problem (PR #15).

## 4. Signed, installable packages for Windows and macOS

Status: Approved, not started. **Lowest priority: take this up only after every other
open item on this board is closed.** It changes how the application is distributed, not
what it does, and nothing here blocks the data work.

### Where v1.1.1 actually stands

Tested by the owner on Windows after the release: **no false positive.** The
`NSE_BSE_Downloader.exe` from `NSE_BSE_Downloader-1.1.1-windows-x64.zip` copies and
runs without adding a Defender exclusion, and Defender does not delete it. So the
Nuitka onefile build is not being classified as malware, and neither a submission to
Microsoft nor a move to standalone-folder packaging is needed for that reason.

What remains on first run is the **unknown-publisher warning** — SmartScreen saying the
file has no reputation, not Defender saying it is dangerous. macOS says the equivalent
through Gatekeeper. Only a signature fixes either, which is what this item is for.

### Track A — Windows: signature, then an installer

- Apply to **SignPath Foundation**, which signs open-source projects at no cost. This
  project looks eligible: GPL-3.0, public repository, built by a public CI from a
  tagged commit. Eligibility is theirs to judge, so treat approval as the first gate
  and do not plan the rest around it until it lands.
- **The existing workflow cannot use it as written.** `trusted-release-candidate.yml`
  expects `WINDOWS_CERTIFICATE_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD` — a PFX file
  in a secret. Since June 2023 a code-signing key must live on a hardware token or in a
  cloud HSM, so no CA hands out a PFX any more, and SignPath signs through its own
  service. The Windows half of that workflow therefore needs rewriting around a signing
  service rather than a decoded certificate file. The macOS half is unaffected.
- Only then produce an **installer** (Inno Setup, NSIS or MSI). Note that an installer
  is itself an executable and needs signing too; an unsigned installer wrapping a signed
  application is worse than the ZIP we ship today, because it puts an unsigned binary in
  front of the user. Keep the ZIP as well — it needs no elevation and no uninstall.
- Reputation accumulates per publisher and per download volume. Expect the warning to
  fade rather than vanish on the first signed release, unless an EV certificate is
  bought, which SignPath Foundation does not provide.

### Track B — macOS: Apple Developer ID, then a .dmg

- **SignPath cannot sign this.** Gatekeeper and `notarytool` accept only a Developer ID
  certificate issued by Apple through the Apple Developer Program, which no third-party
  CA can issue. The free route that exists for Windows has no macOS equivalent; this
  track costs $99/year or does not happen.
- The signing and notarization path is already implemented in
  `trusted-release-candidate.yml` and documented in
  [RELEASE_TRUST_ASSETS.md](RELEASE_TRUST_ASSETS.md); it waits only on credentials.
- A **.dmg** is packaging, independent of signing, and can be built first. An unsigned
  .dmg still triggers Gatekeeper, so on its own it only replaces one right-click→Open
  with another. Its real value is a drag-to-Applications layout instead of a folder the
  user places by hand.

### Required evidence

- The signed Windows artifact verifies under the Authenticode policy, with an RFC 3161
  timestamp, so it stays valid after the certificate expires.
- The signed macOS app passes `spctl --assess --type execute` rather than being
  `rejected`, which is what `RELEASE_BUILD_EVIDENCE.md` records today.
- Both installed and launched on a machine that did not build them, with the first-run
  wording that users actually see recorded in `UPGRADE.md` and the release notes.
- `BUILD-METADATA.json` records `trust_status: signed`, and the release evidence file
  states which artifacts are signed and which are not. A release where only some
  platforms are signed must say so plainly.
