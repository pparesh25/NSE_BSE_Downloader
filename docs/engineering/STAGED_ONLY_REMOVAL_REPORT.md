# Staged-only pipeline and stable-output removal report

Date: 2026-08-04

Branch: `codex/release-hardening`

Release target: unreleased `1.1.0`

## Decision

The Phase 7 staged pipeline is now the only runtime engine. The rollout-only
`pipeline_engine: legacy|staged` flag and gather-after-all runtime path were removed
before the v1.1 branch merged to main. New EQ, SME and FO outputs always use stable
nine-column contracts; the global seven-column compatibility option was removed.

## Removed runtime compatibility

- pipeline engine configuration and worker branching;
- gather-after-all combined reconciliation;
- direct per-date symbol-history and corporate-action fallbacks;
- GUI seven-column checkbox and persisted preference;
- output-column truncation in NSE/BSE EQ, NSE SME and NSE FO;
- explicit legacy JSON state export and legacy-selectable live-soak mode.

## Compatibility intentionally retained

- date-aware readers for official historical NSE/BSE source formats;
- one-time validated JSON-to-SQLite import with untouched backup;
- read-only loading of old seven-column EQ/SME component checkpoints. These are
  upgraded to nine columns in memory and are not rewritten;
- canonical standalone Index files remain seven columns.

Saved obsolete preferences and config keys are ignored. New output files retain
optional delivery or FO open-interest columns and leave their values blank when the
corresponding option is disabled or data is unavailable.

## Correctness and reliability findings

The previous intermediate `Download completed with errors` message was caused by
run-scoped history/action stages still being pending. Core completion now treats those
stages as deferred only while a staged coordinator owns them, and the GUI reports
`Daily files ready; waiting for staged finalization` until final typed outcomes exist.

Coverage runs also exposed SQLite connections that committed but were not explicitly
closed. Pipeline-state and history-journal contexts now commit/rollback and always
close; warning-as-error transactional tests pass.

The first isolated live verification encountered one transient BSE corporate-action
timeout. Public outputs remained complete and SHA-identical, while the typed outcome
correctly became partial. Corporate-action fetches now perform one bounded retry for
timeouts and aiohttp client errors, record retry telemetry and always expose a
non-empty terminal error.

## Verification evidence

- Primary environment: full suite pass.
- Compatibility environment: full suite pass.
- Ruff: pass.
- mypy: pass.
- Coverage gate: 73.88%, above the required 70%.
- Nuitka command validation: darwin, windows and linux pass; no build started.
- Deterministic transport failure fixtures: included in the full suite.
- Staged publication benchmark: all 1, 20 and 101-day single-segment and Select All
  cases have exact golden SHA, row-count and column-order parity; cache cap pass.
- Batched history benchmark: 20 and 100-day output parity pass.
- Fresh isolated live Select All soak for 2026-07-31: 3/3 success, zero silent skips,
  six public outputs per run, identical output SHA, 17.817-second median wall time and
  15.019 ms maximum run event-loop-lag p95.

All tests, benchmarks and live verification used temporary roots under `/tmp`. No
files under `/Users/paresh/NSE_BSE_Data` were modified.

## Merge gate

Before main merge, rerun both full environment suites after this report is added,
commit and push the staged-only changes, verify GitHub checks, self-review the complete
main diff and merge only while the branch remains clean and main has not diverged.
