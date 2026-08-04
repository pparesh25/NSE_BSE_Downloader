# Canonical repository migration record

Date started: 2026-08-05 (Asia/Kolkata)
Status: Release artifact validation in progress; release notification not published

## Objective

Make `pparesh25/NSE_BSE_Downloader` the canonical repository for the validated
PySide6 v1.1 application while retaining its existing users, issues, forks, releases,
and v1.0.1 update-notification endpoint.

## Source and target

- Canonical target: `pparesh25/NSE_BSE_Downloader`
- PySide6 source: `pparesh25/NSE_BSE_Downloader_PySide6`
- Target pre-port main: `b18cb871347da162b8a0da7d0b6222909f7376a6`
- Source pre-port main: `b7caca536fa75f58929e72cd78e64cd374b7f4b1`
- Migration branch: `codex/pyside6-port-v1.1.0`
- History-preserving import commit: `8b338e345015b0e4639720c78310b617212e85bd`

The import commit has both pre-port commits as parents and its tree exactly matches
the PySide6 source tree before canonical-repository retargeting.

## Published rollback references

Canonical target repository:

- Branch: `archive/pyqt6-v1.0.1`
- Tag: `pyqt6-pre-pyside6-port-2026-08-05`

PySide6 source repository:

- Branch: `archive/pyside6-pre-port-v1.1.0`
- Tag: `pyside6-pre-port-2026-08-05`

The existing v1.0.1 release and its assets remain untouched.

## Update transition contract

- `version.py` must retain `__version__`, `__build_date__`, and `VERSION_HISTORY` so
  installed v1.0.1 clients can detect v1.1.0 from the canonical repository.
- The v1.1 updater must use `pparesh25/NSE_BSE_Downloader` for metadata and immutable
  release/tag validation.
- `__update_url__` and `__update_sha256__` must remain blank until the final release
  artifact exists and its SHA-256 has been independently verified.
- A mutable `main` archive is not an approved executable update artifact. The old
  client may still download that archive as part of its legacy behavior, so release
  notes must direct users to the official platform-specific GitHub Release asset.
- Merging the migration branch or increasing the canonical `main/version.py` version
  publishes the old-client notification. Neither action is allowed before release
  assets and migration evidence are ready.

## User-data compatibility contract

- Development, CI, and migration tests must never modify a real
  `~/NSE_BSE_Data/` tree.
- Existing `~/.nse_bse_downloader/user_preferences.json` choices must be schema-
  validated and migrated without losing recognized v1.0.1 settings.
- Existing seven-column daily files remain valid and read-only until a user explicitly
  downloads that date again.
- Re-downloaded EQ, SME, and FO dates use the current stable nine-column output
  contract.
- Obsolete preferences may be discarded only when they no longer control a supported
  runtime behavior.

## Required validation before merge

1. Run focused updater, preference, legacy-file, packaging-readiness, and migration
   tests.
2. Run the full suite in `opentrader313` and `mark_screener` with temporary data roots.
3. Run Ruff, mypy, and the coverage gate.
4. Run the 1/20/101-day deterministic benchmark and the history benchmark.
5. Perform a read-only inventory and copied-profile upgrade test using representative
   v1.0.1 data; never test against live user files in place.
6. Review every hard-coded repository URL and release metadata field.
7. Push only the migration branch and open a draft pull request against canonical
   `main`.

## Validation evidence: 2026-08-05

- Focused updater, version bridge, v1.0.1 preference, and legacy daily-file suite:
  28 tests passed in both `opentrader313` and `mark_screener`.
- Full suite: 174 tests passed in `opentrader313` and 174 tests passed in
  `mark_screener`.
- Ruff: passed.
- mypy: passed for all configured production modules.
- Coverage: 73.87%, above the required 70% gate.
- Staged benchmark: all 1, 20, and 101-day single-segment and Select-All cases had
  exact golden SHA/row/column parity; the prepared-frame cache stayed within its
  four-date cap.
- History benchmark: 20 and 100-day output parity passed. Batched history work used
  four reads and three writes, compared with 61/61 and 301/301 incremental
  reads/writes respectively.
- Packaging command validation: Darwin, Windows, and Linux passed in dry-run mode;
  no Nuitka compilation was started.
- Repository URL audit: active updater, HTTP user agent, README, and updater security
  tests target `pparesh25/NSE_BSE_Downloader`. The PySide6 URL remains only in a
  historical Phase 7 handoff record.
- All automated tests and benchmarks used temporary roots under `/tmp`; no real user
  data was modified.
- Additional user-reported evidence: a six-month all-segment download completed
  without errors and produced symbol-wise histories before this migration began.

The local code-validation gates are complete. Actual release builds, platform smoke
tests, signing/notarization decisions, release-asset checksums, and final update
metadata remain release blockers.

The first unsigned local macOS ARM64 build, updater-compatible archive work, trust
findings, and clean multi-platform workflow are recorded in
`RELEASE_BUILD_EVIDENCE.md`. That local build is evidence only and is not approved
for publication.

## Release blockers

- Produce and test the intended platform release assets.
- Decide whether the transition release remains notification-only or introduces
  platform-specific verified update metadata.
- Record SHA-256 for every published asset.
- Complete applicable signing and notarization gates.
- Update the v1.1 release date/build metadata only when the release date is known.
- Keep the pull request unmerged and the release unpublished until all blockers are
  closed.

## Rollback

Before release, close the draft pull request and delete only the migration branch if
the port is rejected. After release, restore from the published archive branch/tag in
a normal reviewed rollback commit; do not force-push canonical `main`.
