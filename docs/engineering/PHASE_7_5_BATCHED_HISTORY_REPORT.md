# Phase 7.5 batched histories and corporate-action evidence

Date: 2026-08-04 (Asia/Kolkata)
Branch: `codex/release-hardening`
Pipeline engine default: `legacy`

## Scope and rollout boundary

Phase 7.5 adds run-scoped symbol-history batching only when
`pipeline_engine: staged` is selected. The default legacy engine retains its original
per-date `SymbolHistoryStore.upsert()` path. Scheduling, public daily/combined files,
raw/component formats and corporate-action factor/rounding rules are unchanged.

Staged runs now checkpoint each validated EQ/SME frame as the existing checksummed raw
snapshot, queue only its path/hash in SQLite/WAL, and publish histories after all core
daily work settles. Daily output can therefore remain available with a visible partial
result if optional history or action work fails.

All tests and benchmarks used pytest temporary directories or isolated `/tmp` roots.
`/Users/paresh/NSE_BSE_Data` was not read or modified.

## Batch and recovery design

- One shared `HistoryBatchCoordinator` collects all staged EQ/SME dates in a worker
  run, including delivery revisions.
- The history journal shares `pipeline_state.sqlite3` WAL durability. Pending entries,
  prepared batches and per-symbol completion checkpoints are transactional tables.
- Final history work groups all queued dates, reads the registry and applied-action
  ledger once, caches each affected history, and atomically rewrites each final symbol
  at most once in a normal batch.
- Rename rows merge old/new histories in memory. Final files publish before old aliases
  are removed, and the registry publishes last, keeping restart recovery deterministic.
- A crash after a symbol file publish but before its checkpoint may repeat that one
  idempotent write; a checkpointed symbol is skipped and only remaining symbols resume.
- Corporate-action feeds are fetched only after price tasks and history commit. Feed
  results are combined and `CorporateActionEngine.apply()` runs once per exchange;
  its existing prepared/committed checksum journal continues to provide exactly-once
  recovery.

## Reproducible I/O benchmark

```text
/Users/paresh/miniforge3/envs/opentrader313/bin/python \
  tools/phase7_history_benchmark.py --output /tmp/<isolated-root>
```

The fixture uses three symbols, a stable-identifier rename, one existing seed date,
and then 20 or 100 incoming dates.

| Incoming days | Legacy read calls | Legacy writes | Staged read calls | Staged writes | Legacy seconds | Staged seconds | Byte parity |
|---:|---:|---:|---:|---:|---:|---:|---|
| 20 | 61 | 61 | 4 | 3 | 0.258188 | 0.078785 | exact |
| 100 | 301 | 301 | 4 | 3 | 1.302646 | 0.341161 | exact |

SQLite/history telemetry counts three existing-file reads and three final writes in
both staged cases; the fourth read call checks the not-yet-existing renamed target.
Thus staged symbol I/O scales with affected symbols, not dates.

Two independent benchmark runs produced identical timing-excluded JSON. Every final
symbol filename, SHA-256, row count and column order matched legacy:

| Days | File | Rows | SHA-256 |
|---:|---|---:|---|
| 20 | `aaa-new.txt` | 21 | `298379acbec8a4aa5215ab9323f97a646510265572abde461febce7e37e0248e` |
| 20 | `bbb.txt` | 21 | `a2302670a0f1787af06bb8637412f75d94f17612216ebd0e91b6e2200773b070` |
| 20 | `ccc.txt` | 21 | `b6a8d8fc486fef11c21339ff2025d879f9b356b78c8ae1d6eefac94534118d10` |
| 100 | `aaa-new.txt` | 101 | `104dacea070dd672301e7cf22c908d99fede64037fc873d91145f84c1b3946f4` |
| 100 | `bbb.txt` | 101 | `9de681f02545a569440d2a9fe1a4db1f15d8d139399cf5674f02b2fdca1438b8` |
| 100 | `ccc.txt` | 101 | `8ebd6428563279f771952eff990bb2d9b4d262aa5da3780616fe7388cfa739c7` |

Column order remained:
`DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,SERIES,TOTAL_TRADES,QTY_PER_TRADE,DELIVERY_QTY,DELIVERY_PERCENT`.

## Fault and semantic gates

- Injected failure on the second of three symbol writes leaves one completion
  checkpoint. A fresh coordinator writes only the two remaining symbols, clears the
  prepared batch, completes pipeline stages and matches a clean batch byte-for-byte.
- Daily stage completion remains partial while `symbols` is queued and becomes success
  only after history commit.
- A late delivery revision refreshes delivery fields while audited pre-action OHLC is
  reapplied exactly once.
- Worker integration proves the corporate action is fetched/applied only after the
  symbol history exists, and all affected `actions` stages then complete.
- Existing action tests still cover history-publish failure, final-ledger failure,
  unexpected revision rejection, backfill reconciliation and duplicate feed records.

## Verification summary

- Phase 7.5 focused batch/worker suite: **6 passed**.
- Primary `opentrader313` full suite: **160 passed**.
- Compatibility `mark_screener` full suite: **160 passed**.
- Ruff on changed Python files: **pass**.
- mypy across 45 source files: **pass**.
- `compileall` in both environments: **pass**.
- Two-run benchmark SHA/row/column comparison: **exact parity**.

## Review gate

Phase 7.5 acceptance gates pass. Phase 7.6 GUI rollout/default-engine decision and live
soak have not started. The staged engine should remain opt-in until Phase 7.6 A/B and
live Select All soak gates are reviewed.
