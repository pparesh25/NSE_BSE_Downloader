# Phase 5 step 1 evidence — dual-writing the EOD database

Date: 2026-08-20 (Asia/Kolkata)
Branch: `feat/sqlite-system-of-record`
Setting: `download_options.dual_write_eod_database` (default `true`)

## Scope and safety

Step 1 of the four-step migration in
[PROJECT_REVIEW_2026-08-06.md](PROJECT_REVIEW_2026-08-06.md) §7: write the database
alongside the existing text files, from the canonical frames already in memory, and
have **nothing read it**. Publication, symbol histories, rebuilds, corporate actions
and `--audit` are untouched, so the store can be deleted at any time and the setting
turned off with no loss but the mirror.

Nothing in this step changes a published byte. Every measurement below was taken
either in a pytest temporary directory or in a scratch directory outside the data
root; `~/NSE_BSE_Data` was read and never written.

## What the measurements changed about the proposed design

The schema in §7 of the review proposes `PRIMARY KEY (exchange, segment,
security_id, trade_date)` with `security_id` described as era-invariant. Measuring the
owner's tree first — 208,555 rows across 29 trading days and three segments — found
three things that make that key wrong as written.

| Measured | Result |
| --- | --- |
| NSE SME rows carrying `SECURITY_ID` | **0 of 12,418.** `ISIN` is empty too. Every row would collide on one key. |
| NSE ISINs mapping to more than one `SECURITY_ID` | **117 of 2,769.** NSE numbers a security per *(security, series)*. |
| `SECURITY_ID` values mapping to more than one ISIN | **11 NSE, 10 BSE.** ISIN moves on a face-value change; the code does not. |

`AARTECH` is the whole problem in one security: it moved from series `EQ` to `BE` on
2026-07-10, keeping ISIN `INE01C001026` and changing id from 17145 to 17164. Keying on
the id splits its history; keying on the ISIN would fold together the 21 securities
whose ISIN changed. **Neither identifier alone identifies a company**, which the
codebase already knows — `symbol_history._stable_keys_from` registers both and lets the
registry merge them.

So the store does not invent a third identity rule. `security_key` is
`ID:<security_id>`, else `ISIN:<isin>`, else `SYM:<symbol>`, which is a stable identity
for a **published row** — measured unique within every date, on every segment. `isin`,
`security_id`, `symbol` and `series` are stored as columns so that deciding two keys
are the same security stays in the one place that already does it correctly.

### Storage types, likewise measured

Every value in every price column — 208,555 each of `OPEN`, `HIGH`, `LOW`, `CLOSE`,
`PREV_CLOSE`, `TURNOVER`, `QTY_PER_TRADE`, and 199,537 of `DELIVERY_PERCENT` —
round-trips through `float` unchanged. `VOLUME`, `TOTAL_TRADES` and `DELIVERY_QTY` do
not, and the reason is not precision: the sources write `7`, and `repr(7.0)` is
`'7.0'`. All three are whole numbers well inside 2^53, so they are stored as
`INTEGER`. That makes the stored value exact and leaves how each exchange spells it to
the export step, which is where it belongs.

An unpublished field stays `NULL`, never `0` — a BSE index publishes no turnover, and
a zero would claim it traded none.

## `WITHOUT ROWID`, and why the statistics matter more

Both table shapes were built from the same 208,555 rows and queried:

| | rowid table | `WITHOUT ROWID` |
| --- | --- | --- |
| one symbol's whole series | 52.04 ms | **5.61 ms** |
| one bhavcopy by date | 6.70 ms | 11.80 ms |
| bulk load | 1.17 s | 1.64 s |
| file size | 44.1 MB | 43.4 MB |

Clustering on the primary key puts one security's rows physically together, which is
the query this project exists to serve. Across 8,000 symbols that is the difference
between a seven-minute export pass and a forty-five-second one. The by-date query gets
slower and stays trivial.

A second finding came out of pinning that with `EXPLAIN QUERY PLAN`: **a clustered
table with no statistics makes the planner do the wrong thing silently.**
`WHERE exchange=? AND segment=? AND trade_date=?` reads as a primary-key prefix, so it
scans every date that exchange ever published rather than seeking one. On 28 dates
that is a 28-fold overscan that still answers in 13 ms; on a multi-year archive it is a
several-thousand-fold one.

`PRAGMA optimize` is the usual remedy and **was measured not to work here**. This store
opens a connection per transaction, so each one sees only its own small delta, declines
to re-analyse, and leaves statistics frozen at what the first 4,279 rows looked like —
which is worse than none, because it is what kept the planner on the wrong path. A
scheduled `ANALYZE` on a doubling threshold replaces it: ~120 ms per 200,000 rows,
about a dozen runs on the way to a full archive, and the row tally is incremented
inside the same transaction as the rows it counts so a rolled-back date cannot inflate
it.

## Loading the real archive

The owner's 84 internal snapshots, replayed into a scratch database:

```
snapshots       84
rows written    208,555
wall time       4.79 s   (median 51.8 ms per date-segment, max 184 ms)
size            43.4 MB
plan            SEARCH eod USING INDEX idx_eod_date
```

Extrapolating honestly: 43.4 MB / 208,555 rows is **218 bytes per row including both
indexes**, so a full NSE history from 1994 plus BSE from 2016 — roughly 33 million rows
— lands near **7 GB**, not the 3 GB estimated in the review. The estimate there
assumed a narrow integer-keyed row; text keys and two indexes are what account for the
difference. It is still an ordinary size for a single-user desktop archive, and it is
worth knowing before the backfill rather than during it.

### Fidelity spot-check

800 published symbol files were compared against the mirror on `(date, close,
volume)`: **0 files with any row missing.** That is not the byte-parity proof — that is
step 2's job — but it establishes that the dual-write is faithful before anything is
built on top of it.

## Gates

Both interpreters, matching CI:

- `pytest` with coverage — **532 passed**, total coverage 78.82% (floor 70%).
- `ruff check .` — all checks passed.
- `mypy` over 59 source files — no issues.
- `main.py --smoke-gui --config config.yaml` — exit 0.

The 23 new tests in `tests/test_eod_store.py` pin the identity cases above as the real
securities they were measured from, the integer/`NULL` storage rules, transaction
atomicity, the clustering, the index plans, and — the promise of this step — that a
mirror which raises leaves the same published bytes and the same pipeline verdict as a
run with the mirror switched off.

## What this step deliberately does not do

- **Nothing reads the database.** A write failure is logged and the run continues.
  When step 3 makes it the publication source, that becomes a hard failure.
- **Rows are stored per segment, not per published file.** A combined `NSE/EQ` file
  also carries appended SME and index rows, so regenerating it is a union of segments;
  that belongs to the export step, where the append options are already modelled.
- **No backfill of existing dates.** The mirror fills as dates are downloaded. The
  84 snapshots already on disk can seed it, which is a natural first task for step 2
  since it needs a populated database to diff against.
- **`sme_add_suffix` remains a naming input.** NSE SME publishes no identifier, so its
  key is `SYM:<published name>`; flipping that preference mid-history would create a
  second key for the same security. Pre-existing, and surfaced here because step 2 is
  where it becomes visible.

## Next

Step 2 — `export_daily(date)` / `export_symbol(symbol)` regenerating text from the
database, re-exported and SHA-diffed against the existing files. The formatting
question this step defers (`7` versus `7.0`, per exchange) is the first thing that
harness will answer.
