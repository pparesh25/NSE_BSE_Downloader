# Phase 7 conversation handoff

## Project state

- Repository: `/Users/paresh/Documents/New project/NSE_BSE_Downloader 1.0.1 pyside6`
- Current branch: `codex/release-hardening`
- Remote repository: `pparesh25/NSE_BSE_Downloader_PySide6`
- Primary runtime for GUI/live work:
  `/Users/paresh/miniforge3/envs/opentrader313`
- Secondary compatibility runtime:
  `/Users/paresh/miniforge3/envs/mark_screener`
- Phase 6 source/Nuitka preparation is complete, but no actual Nuitka build is
  authorized yet.

## Required reading before implementation

1. `DOWNLOAD_PIPELINE_ARCHITECTURE_PLAN.md` — approved Phase 7 architecture,
   evidence, phases and acceptance gates.
2. `CODE_REVIEW_REMEDIATION_PLAN.md` — completed hardening history and preserved
   reliability guarantees.
3. `IMPLEMENTATION_PLAN.md` — output schemas, delivery/OI, symbol history and
   corporate-action contracts.

## Approved direction

The user approved the architecture plan. Start with **Phase 7.0 only**:

- add behavior-neutral structured timing/attempt/event-loop telemetry;
- add deterministic transport-failure fixtures/harness;
- establish repeatable 1-day, 20-day and 100+ day benchmarks for single segment and
  Select All;
- capture output SHA, row count and column-order parity;
- add `pipeline_engine: legacy|staged` feature-flag groundwork, initially with
  `legacy` as the default; after Phase 7.6 acceptance, `staged` became the default,
  and the fallback was removed before the unreleased v1.1 branch merged to main;
- produce a baseline report before implementing Phase 7.1 shared transport.

Do not jump directly to per-date join, SQLite migration or batched symbol histories.
Those changes belong to later approved phases after the baseline is reviewed.

## Findings that must guide Phase 7

- Current NSE and BSE combined EQ assembly is disk-checkpoint based; NSE is not pure
  in-memory on the current branch.
- GUI launches one task per segment, then waits for every selected segment's full date
  range before combined reconciliation. There is no per-date dependency barrier.
- All segment coroutines share one worker event loop, while pandas transforms, CSV and
  JSON writes, symbol-history rewrites and corporate-action application are synchronous.
  This can starve in-flight HTTP reads and consume the current five-second total timeout.
- A new `AsyncDownloadManager` and HTTP session is created for every date. Concurrency,
  rate limiting and statistics are manager-local, not shared per exchange/host.
- Actual 2026-08-04 pipeline-state inspection found 16 explicit failures; every one was
  a five-second timeout after all three attempts. Retry executes, but GUI visibility and
  contention control are insufficient.
- Actual data contained 3,318 NSE and 4,828 BSE symbol files plus 8,250 history backups.
  Current per-date/per-symbol full read-backup-rewrite is a major synchronous I/O path.
- Preserve atomic publication, restart recovery, deterministic component ordering,
  canonical columns, pending delivery, quarantine/fail-closed behavior and
  corporate-action exactly-once semantics.

## Deferred/unrelated work

- Do not perform an actual Nuitka build, signing or notarization unless the user gives
  separate authorization.
- GitHub Actions currently needs Ubuntu `libEGL.so.1` setup and an explicit pinned
  Ruff/version configuration. The user explicitly deferred these CI fixes; do not mix
  them into Phase 7.0.
- GUI default sizing/process-title polish was already completed before Phase 7.

## Working rules for the new conversation

- Inspect `git status`, current branch and recent commits before editing; preserve any
  user/uncommitted changes.
- Use `/Users/paresh/miniforge3/envs/opentrader313` for the primary test run and the
  `mark_screener` environment for compatibility gates.
- Do not modify user data under `/Users/paresh/NSE_BSE_Data` during baseline inspection.
  Benchmarks/fault tests must use isolated temporary data roots unless a live run is
  explicitly approved.
- Make small reviewable commits. Do not push unless requested.
- Report evidence separately for network time, prepare time, component/final writes,
  manifest/state time, symbol-history time, corporate-action time and event-loop lag.

## First deliverable

An evidence-backed Phase 7.0 baseline report and instrumentation commit(s) that do not
change output bytes or scheduling behavior. Stop for review before starting Phase 7.1.
