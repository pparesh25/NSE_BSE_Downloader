# Phase 7.4 transactional pipeline-state evidence

Date: 2026-08-04 (Asia/Kolkata)
Branch: `codex/release-hardening`
Pipeline engine default: `legacy`

## Scope and safety

Phase 7.4 replaces whole-manifest JSON rewrites with a built-in SQLite database in
WAL mode. Each stage mutation now reads, validates, changes and commits its selected
date records inside one `BEGIN IMMEDIATE` transaction. Multi-date stage updates use
one all-or-nothing transaction, and incomplete-date lookup uses an indexed query.

The migration does not change scheduling, combined-file logic, symbol-history or
corporate-action algorithms, public/raw/component file formats, or the
`pipeline_engine` default. Tests and benchmarks used pytest temporary directories or
isolated `/tmp` roots. `/Users/paresh/NSE_BSE_Data` was not read or modified.

## Migration and rollback evidence

- On first open, an existing validated `pipeline_manifest.json` is imported in one
  transaction and read back before commit for exact dictionary parity.
- The original JSON bytes remain untouched during import and all normal SQLite stage
  updates. An exact `pipeline_manifest.pre_sqlite.json` copy is retained.
- `export_legacy_snapshot()` is the explicit rollback/transition-release export path;
  it writes the current validated SQLite state in the legacy v1 JSON shape.
- Empty/non-empty migration races are serialized by `BEGIN IMMEDIATE`, and the
  migration marker prevents repeated import of a valid empty legacy manifest.
- SQLite startup checks schema version, WAL availability and `integrity_check`.
  Structural or logical state corruption fails closed; original DB/WAL/SHM bytes are
  retained and copied to `.state/quarantine/pipeline_sqlite` when possible.

## Fault and query gates

The Phase 7.4 acceptance suite verifies:

- exact legacy JSON import, original-byte preservation and WAL activation;
- explicit current-state export for rollback;
- successful and failed multi-date batch atomicity;
- an indexed incomplete-date query over 120 date records and restart parity;
- abrupt process exit during an uncommitted transaction, followed by correct rollback
  and resume;
- corrupt main-DB fail-closed quarantine, byte-preserving backup and controlled
  recovery by rebuilding from the untouched legacy JSON.

During fault-test development, SQLite also recovered a damaged main file while a
valid WAL was present. The quarantine fixture therefore checkpoints and removes test
sidecars before corrupting the temporary main file so it deterministically exercises
the unrecoverable-main-DB path.

## Output-parity benchmark

Reproduction used the existing offline harness twice with independent temporary
roots:

```text
/Users/paresh/miniforge3/envs/opentrader313/bin/python \
  tools/phase7_benchmark.py --output /tmp/<isolated-root>
```

| Mode | Days | Wall seconds | Output rows | First output SHA-256 |
|---|---:|---:|---:|---|
| single segment | 1 | 0.014742 | 4 | `074495a3409e2537d08eb31ccd5869c933772d24895fbf987ba7fedb9d3eff8d` |
| Select All | 1 | 0.020330 | 8 | `69a000926d5453ba4df25601ae70953adc02ba9e146e0dea1132b06049ba7a04` |
| single segment | 20 | 0.147954 | 80 | `074495a3409e2537d08eb31ccd5869c933772d24895fbf987ba7fedb9d3eff8d` |
| Select All | 20 | 0.384014 | 160 | `69a000926d5453ba4df25601ae70953adc02ba9e146e0dea1132b06049ba7a04` |
| single segment | 101 | 0.743137 | 404 | `074495a3409e2537d08eb31ccd5869c933772d24895fbf987ba7fedb9d3eff8d` |
| Select All | 101 | 1.940610 | 808 | `69a000926d5453ba4df25601ae70953adc02ba9e146e0dea1132b06049ba7a04` |

After excluding wall-clock timing, both benchmark result documents were identical.
Every SHA and row count also matches the Phase 7.0 baseline. All output files retain
the canonical column order:
`SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,DELIVERY_QTY,DELIVERY_PERCENT`.

## Verification summary

- Primary `opentrader313` full suite: **154 passed**.
- Compatibility `mark_screener` full suite: **154 passed**.
- Focused transactional-state acceptance suite: **6 passed**.
- Ruff on Phase 7.4 files: **pass**.
- mypy across 44 source files: **pass**.
- `compileall` in both environments: **pass**.
- Two-run output SHA/row/column-order comparison: **exact parity**.

## Review gate

Phase 7.4 acceptance gates pass. Phase 7.5 batched symbol histories and corporate
actions have not started. Review this report and the Phase 7.4 commit before enabling
or beginning Phase 7.5.
