# Phase 7.6 GUI rollout and soak evidence

Date: 2026-08-04 (Asia/Kolkata)
Branch: `codex/release-hardening`
Pipeline engine default: `legacy`

## Scope and safety

Phase 7.6 adds typed live attempt/stage/retry status, queue depth and progress ETA,
exact failed/pending-date retry selection, bounded-cache rollout evidence, and
reproducible offline/live benchmark harnesses. It does not change normal date
scheduling, combined-file semantics, source rules, history algorithms or state
storage. The staged engine remains opt-in.

All tests and benchmarks used pytest temporary directories or isolated `/tmp` roots.
`/Users/paresh/NSE_BSE_Data` was not read or modified.

## GUI and retry behavior

- Download attempt, maximum attempt count, retry reason/backoff, durable pipeline
  stage, executor queue depth/start/finish, history queue and date-join completion are
  translated from typed telemetry into segment-specific GUI status.
- Telemetry subscribers are thread-safe and non-fatal: a display/listener exception
  cannot replace the real pipeline outcome.
- Progress text reports completed percentage, remaining item count and estimated time.
- `Retry Failed/Pending` exposes only `partial` and `failed` dates from the completed
  run. Successful and terminal skipped dates are excluded.
- Retry bypasses automatic range/gap discovery and sends only the confirmed exact
  dates. When a combined dependency needs repair, those same dates are closed over the
  configured EQ/SME/Index dependency set; no automatic or wider date range is added.

## Reproducible offline A/B benchmark

```text
/Users/paresh/miniforge3/envs/opentrader313/bin/python \
  tools/phase7_ab_benchmark.py --output /tmp/<isolated-root>
```

The harness compares legacy gather/reconcile with staged per-date publication for 1,
20 and 101 days. Select All covers NSE EQ+SME+Index and BSE EQ+Index combined
publication. Segment-major staged arrival intentionally leaves many dates open so the
prepared-frame cache cap is exercised.

| Mode | Days | Legacy seconds | Staged seconds | Legacy peak MiB | Staged peak MiB | Staged cache peak | Outputs | SHA/row/column parity |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Single segment | 1 | 0.037 | 0.027 | 1.25 | 1.09 | 0 | 1 | exact |
| Select All | 1 | 0.126 | 0.097 | 1.28 | 1.21 | 1 | 2 | exact |
| Single segment | 20 | 0.552 | 0.548 | 1.33 | 1.28 | 0 | 20 | exact |
| Select All | 20 | 2.553 | 2.446 | 1.39 | 1.46 | 4 | 40 | exact |
| Single segment | 101 | 2.869 | 2.804 | 1.39 | 1.36 | 0 | 101 | exact |
| Select All | 101 | 13.243 | 12.799 | 1.52 | 1.90 | 4 | 202 | exact |

The configured cache cap is four dates and the observed maximum was four. All 366
legacy/staged output pairs were byte-identical, with matching row count and canonical
column order:
`SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,DELIVERY_QTY,DELIVERY_PERCENT`.

Representative deterministic outputs:

| Output | Rows | SHA-256 |
|---|---:|---|
| Single NSE EQ | 4 | `5c11dd4377bc827e112c1f99b222215efed17e16f4a952cb2e6ffa2dc3935ea8` |
| Select All NSE EQ | 8 | `b499636adc83674d9297be599d024268629405078975a67841605bba48e066fa` |
| Select All BSE EQ | 6 | `76241df4a77cfc46de584e8f03194f6a08394748a8d109fbcea1d37595a1526f` |

## Live Select All soak

```text
/Users/paresh/miniforge3/envs/opentrader313/bin/python \
  tools/phase7_live_soak.py --date 2026-07-31 --runs 20 --engine staged \
  --output /tmp/<isolated-root>
```

Each run used a fresh data/state root and downloaded all six application segments. The
harness persisted each run immediately, audited one typed result for every requested
segment, captured attempt/retry/lag telemetry and hashed all public daily outputs.

- Runs: **20/20 successful**.
- Typed segment outcomes: **120/120 success**.
- Silent missing segment/date outcomes: **0**.
- Visible errors: **0**.
- Download attempts: **180**; live retries required: **0**.
- Public outputs: **6 per run**, byte-identical across all 20 runs.
- Median wall time: **24.857 seconds**.
- Worst per-run event-loop lag p95: **13.282 ms**, below the 100 ms gate.

## Live legacy control and rollout decision

A fresh-root one-day legacy Select All control for the same date also produced six
successful outputs, byte-identical to staged. Legacy completed in **20.671 seconds**;
the matching staged run completed in **24.467 seconds**. The staged path therefore did
not demonstrate the material live performance improvement required for default
promotion, despite passing correctness, reliability, responsiveness and cache gates.

Decision: keep `pipeline_engine: legacy` as the default. Staged remains available for
controlled testing. Legacy removal and staged-default promotion are not approved by
this report.

## Fault, calendar and source gates

- Timeout, slow response, HTML-200, empty ZIP, CRC, 403, 404, 429 and 500 fixtures and
  transport retry tests passed.
- The deterministic failure/transport set passed **9/9 in five consecutive runs**.
- Calendar and NSE/BSE source-era cutover tests passed as part of a **24-test** focused
  gate.
- Retry telemetry exposes attempt count, reason, next attempt and delay without
  changing retry policy.

## Environment verification

- Primary `opentrader313`: **165 passed**; Ruff pass; mypy pass across 44 source files;
  compileall pass.
- Compatibility `mark_screener`: **165 passed**; compileall pass.
- Ruff and mypy are not installed in `mark_screener`; the same source tree passed both
  static checks with the primary toolchain.
- Changed behavior-focused GUI/telemetry/date-join suite: **29 passed**.
- `git diff --check`: pass.

## Review gate

Phase 7.6 implementation and reliability evidence are complete. The safe next step is
to profile the staged live overhead—especially run-final history/action work and
one-day coordination startup—then repeat live A/B. Do not switch the default or remove
legacy until staged shows a material repeated live advantage while retaining these
parity and soak results.
