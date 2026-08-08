# Pending engineering tasks

Last reviewed: 2026-08-06 (Asia/Kolkata)
Repository branch at review: `codex/pyside6-port-v1.1.0`

## Working convention

All new or modified repository documentation, source-code comments, and Git commit
subjects and bodies must be written in English. Historical documents do not require
a language-only rewrite unless they are being substantively revised. Localized
user-facing application text is outside this engineering-record convention.

This file is the central index for approved but incomplete engineering work. Detailed
research and evidence files remain authoritative references, but every deferred topic
must also be recorded here so it is not lost between phases.

## 0. v1.1.0 release publication — published 2026-08-09

Status: **Done.** Paused on 2026-08-06 so that code-level defect remediation could go
first; resumed once Phase 3 of
[CODE_DEFECT_REMEDIATION_PLAN.md](CODE_DEFECT_REMEDIATION_PLAN.md) closed, which
removed every defect the pause existed to avoid shipping.

Published from tag `v1.1.0` at commit `059f497`:
<https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.1.0>. Asset digests
and the verification performed on them are in
[RELEASE_BUILD_EVIDENCE.md](RELEASE_BUILD_EVIDENCE.md).

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

## 1. BSE SME/Startup classification and symbol naming

Status: Research complete; product decisions and implementation pending.

Detailed record:
[BSE_SME_STARTUP_SUFFIX_FINDINGS.md](BSE_SME_STARTUP_SUFFIX_FINDINGS.md)

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
  is missing from the accepted group set and can therefore be dropped.
- Delivery matching uses BSE security code, so display-name suffixing must remain
  separate from delivery identity.
- Price bands and old settlement-cycle wording must not be used as classification
  keys. Group/series is the authority.

### Decisions still required

1. Choose the public symbol policy for Startup groups:
   - keep `MS` and `TS` symbols unchanged;
   - append `_STARTUP`; or
   - deliberately append `_SME` to all four groups and coordinate every downstream
     consumer.
2. Decide whether users need a separate SME/Startup selector and output, or only
   correct inclusion and classification within BSE Equity.
3. Decide whether existing unsuffixed BSE SME history should remain unchanged for
   historical dates or be migrated through an audited rebuild.

### Approved implementation direction

- Add explicit constants for regular SME groups, Startup groups, and their union.
- Add `TS` to accepted BSE cash groups.
- Unless the Startup naming decision changes it, preserve `Mark Python` compatibility
  by applying `_SME` only to `M` and `MT`.
- Apply naming after source-era mapping and before canonical validation; make the
  transformation idempotent.
- Preserve one BSE cash download and the existing security-code delivery join.
- Use the same normalized identity in public files, component checkpoints, combined
  outputs, raw snapshots, symbol histories, retries, and rebuilds.
- Clarify the GUI label/help text to state that BSE Equity includes regular SME and
  Startup securities.
- Do not add a suffix checkbox unless an explicit compatibility requirement is
  approved.

### Required evidence before release

- Cover `M`, `MT`, `MS`, `TS`, and a main-board control across all three BSE source
  eras.
- Test suffix idempotence, mutual-fund exclusion, delivery matching, corporate-action
  lookup, stable-ID history migration, restart recovery, and duplicate prevention.
- Re-run 1/20/101-day single-segment and Select-All parity benchmarks. Explain every
  intentional SHA change caused by naming or newly included `TS` rows.
- Run the full suite in `opentrader313` and `mark_screener` with isolated temporary
  data roots.
- Never modify `/Users/paresh/NSE_BSE_Data` during development or testing. Produce a
  read-only migration inventory and dry-run report before any approved user-data
  migration, and stop on ambiguous identities.

### Separate scope

BSE FO remains a separate research and implementation task. It is not part of the
cash-market SME/Startup naming work.

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
