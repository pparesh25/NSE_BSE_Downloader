# Phase 7.6 staged finalization optimization evidence

Date: 2026-08-04 (Asia/Kolkata)
Branch: `codex/release-hardening`
Pipeline engine default at measurement time: `legacy`

## Scope and safety

This follow-up profiles and optimizes only staged symbol-history and corporate-action
finalization. Normal date scheduling, source selection, combined-file semantics,
history column/rename/action rules, transactional state and retry policy are unchanged.

All profiling, benchmarks and live runs used fresh isolated `/tmp` data roots.
`/Users/paresh/NSE_BSE_Data` was not modified.

## Profile findings

The pre-optimization one-day Select All staged run spent about 20.4 seconds in the
7,426-symbol history batch. A representative cProfile captured 7,426 generic
one-row DataFrame conversions, reads, sorts, deduplication passes and validations.
Independent NSE EQ, NSE SME and BSE action requests were also fetched sequentially.

The resulting changes are deliberately narrow:

- Batched incoming rows accumulate by final symbol path and are converted/concatenated
  once per symbol. Rename paths move pending rows in the same old-to-new order.
- A brand-new one-row staged history uses scalar schema/date/OHLC validation and atomic
  publication without running generic sort/dedup work that cannot change one row.
- Existing files, multiple dates, revisions, collisions and renames retain the full
  generic validation/dedup/backup path.
- Independent corporate-action windows fetch concurrently. Ordered `gather` results
  preserve deterministic aggregation and action application still happens only after
  history commit.
- Fetch and apply outcome/duration telemetry is exported and summarized by the live
  soak harness.

Crash behavior remains idempotent. If termination happens after a fast-path file
replace but before its journal checkpoint, restart sees an existing file and re-enters
the full dedup path before checkpointing it.

## Representative finalization result

Using the three canonical live snapshots from the isolated 2026-07-31 Select All run:

| Metric | Before | Optimized |
|---|---:|---:|
| Symbols/files | 7,426 | 7,426 |
| History writes | 7,426 | 7,426 |
| History finalization | about 20.4 s | 10.94 s median in final soak |
| File SHA parity | baseline | exact for all 7,426 files |

The optimized standalone finalization check completed in 10.738 seconds and every
symbol filename/byte hash matched the pre-optimization output.

## Repeated live A/B

Both engines downloaded all six application segments for 2026-07-31 into a new root
for every run.

| Run | Legacy seconds | Optimized staged seconds |
|---:|---:|---:|
| 1 | 20.401 | 14.843 |
| 2 | 20.248 | 14.228 |
| 3 | 20.125 | 14.160 |
| 4 | 20.370 | 14.102 |
| 5 | 20.134 | 14.279 |
| **Median** | **20.248** | **14.228** |

The staged median was **29.7% faster**. All ten runs succeeded with zero silent
outcomes and exact cross-engine public-output SHA parity. Worst per-run p95 event-loop
lag was 7.89 ms for legacy and 11.20 ms for staged, both well below the 100 ms gate.

## Optimized 20-run staged soak

- Runs: **20/20 successful**.
- Typed segment outcomes: **120/120 success**.
- Silent missing outcomes and visible errors: **0**.
- Attempts: **180**; retries required: **0**.
- Public outputs: **6 per run**, byte-identical across all runs and to legacy.
- Wall time: **14.073 s minimum, 14.410 s median, 15.335 s maximum**.
- History batch median: **10.936 s**.
- Corporate-action fetch critical-path median: **0.532 s**.
- Worst per-run event-loop lag p95: **17.027 ms**.

Compared with the repeated legacy median of 20.248 seconds, the optimized 20-run
staged median remained **28.8% faster**.

## Multi-day and bounded-memory gates

- 20-day history benchmark: legacy 61 reads/writes versus staged 4 reads and 3 writes;
  staged 0.039 s versus legacy 0.238 s; byte parity exact.
- 100-day history benchmark: legacy 301 reads/writes versus staged 4 reads and 3
  writes; staged 0.148 s versus legacy 1.249 s; byte parity exact.
- Offline 1/20/101-day single-segment and Select All A/B: every SHA, row count and
  column order matched; 101-day staged prepared-cache peak remained at the configured
  four-date cap.

## Verification

- Primary `opentrader313`: Ruff pass, mypy pass across 44 source files, compileall
  pass, **169 tests passed** with clean exit on confirmation run.
- Compatibility `mark_screener`: compileall pass, **169 tests passed**.
- Focused history/action/lifecycle suite: **21 tests passed**.
- New tests verify fast-path byte equivalence and numeric validation, plus concurrent
  action-window execution and typed timing telemetry.
- `git diff --check`: pass.

During default rollout verification, the earlier intermittent Qt shutdown warning was
traced to a static delayed update-check timer that could start its `QThread` after a
window had already closed. The timer is now window-owned and cancelled during accepted
close/close-after-worker teardown. Three consecutive primary full-suite runs exited
cleanly with 169/169 tests; no worker remained active in optimized live runs.

## Rollout decision point

The optimized staged engine satisfies correctness, reliability, responsiveness,
bounded-cache and material live-performance gates. Rollout approval was subsequently
given: `staged` is now the default for one transition release while the explicit
`pipeline_engine: legacy` feature-flag fallback and monitoring telemetry remain.
Legacy removal remains a later, separate decision.

Normal configuration:

```yaml
pipeline_engine: staged
```

Immediate compatibility rollback, without removing staged code or changing data:

```yaml
pipeline_engine: legacy
```
