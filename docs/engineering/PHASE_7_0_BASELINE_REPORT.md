# Phase 7.0 baseline evidence

Date: 2026-08-04 (Asia/Kolkata)
Branch: `codex/release-hardening`
Engine flag: `legacy` (default)

## Scope and safety

This baseline adds structured attempt timing, an asyncio event-loop lag sampler,
offline failure fixtures, an isolated benchmark harness, and the
`pipeline_engine: legacy|staged` configuration groundwork. It does not change
scheduling, retry decisions, combined-file logic, symbol history, corporate
actions, or state storage. No request was made to, or write was performed under,
`/Users/paresh/NSE_BSE_Data`.

The benchmark is an offline persistence/reconciliation baseline using generated
canonical frames and a temporary root. It is reproducible evidence for output
parity and local I/O scaling; it is not a live NSE/BSE latency measurement.

## Reproduction

Primary runtime:

```text
/Users/paresh/miniforge3/envs/opentrader313/bin/python \
  tools/phase7_benchmark.py --output /tmp/phase7-baseline-20260804
```

The harness covers 1, 20 and 101 days for `single_segment` and `select_all`, and
records output SHA-256, row count and canonical column order in
`benchmark-results.json`.

## Offline benchmark result

| Mode | Days | Wall seconds | Output rows | First output SHA-256 |
|---|---:|---:|---:|---|
| single segment | 1 | 0.013329 | 4 | `074495a3409e2537d08eb31ccd5869c933772d24895fbf987ba7fedb9d3eff8d` |
| Select All | 1 | 0.016178 | 8 | `69a000926d5453ba4df25601ae70953adc02ba9e146e0dea1132b06049ba7a04` |
| single segment | 20 | 0.139678 | 80 | `074495a3409e2537d08eb31ccd5869c933772d24895fbf987ba7fedb9d3eff8d` |
| Select All | 20 | 0.326736 | 160 | `69a000926d5453ba4df25601ae70953adc02ba9e146e0dea1132b06049ba7a04` |
| single segment | 101 | 0.958533 | 404 | `074495a3409e2537d08eb31ccd5869c933772d24895fbf987ba7fedb9d3eff8d` |
| Select All | 101 | 2.183936 | 808 | `69a000926d5453ba4df25601ae70953adc02ba9e146e0dea1132b06049ba7a04` |

All outputs use the canonical order:
`SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,DELIVERY_QTY,DELIVERY_PERCENT`.
Repeated runs with the same generated inputs must produce the same SHA values.

## Deterministic failure matrix

Offline fixtures cover timeout, slow response, connection reset, HTTP 200 HTML,
empty ZIP, CRC corruption, 403, 404, 429 with `Retry-After`, 500, and cancel.
They are data-only and intentionally do not assert new retry policy; policy
changes belong to Phase 7.1.

## Verification performed

- Primary focused Phase 7, transport, and combined-builder tests: **23 passed**.
- Full regression suite: **133 passed** in both `opentrader313` and
  `mark_screener`.
- `compileall` and changed-file Ruff checks: **pass**.
- A second benchmark run produced identical SHA, row-count, and column-order
  records for all six cases.
- Event records include attempt number, max attempts, URL/date, status, bytes,
  outcome/error type, duration, retry reason, next attempt, and backoff delay.
- Event-loop lag is sampled from a monotonic asyncio timer and exported through
  the telemetry API when requested.

## Review gate

Phase 7.1 shared transport/retry, stage isolation, scheduling changes, and
state/history migrations have not started. This report is the Phase 7.0 review
point; proceed only after baseline evidence and output parity are reviewed.
