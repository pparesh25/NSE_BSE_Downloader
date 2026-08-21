# Phase 5 evidence — dual-write, and regenerating text from the database

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

> **The measured tree no longer exists.** On 2026-08-21 the owner identified those
> 29 dates as download-test files with no further use and had them deleted. The
> numbers below were taken while it existed and stand as the evidence for the design
> decisions they justify, but they cannot be re-run against the same input. Anything
> re-measured later will be against a differently-shaped archive.

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
  84 snapshots that could have seeded it were deleted with the rest of the test tree
  on 2026-08-21, so step 2 begins by downloading a handful of dates to have something
  to diff against.
- **`sme_add_suffix` remains a naming input.** NSE SME publishes no identifier, so its
  key is `SYM:<published name>`; flipping that preference mid-history would create a
  second key for the same security. Pre-existing, and surfaced here because step 2 is
  where it becomes visible.

---

# Step 2 — parity, and what it changed again

Date: 2026-08-21 (Asia/Kolkata)

## The approach

`src/services/eod_export.py` rebuilds the frame and hands it to `DataFrame.to_csv`
with the arguments the publisher uses, rather than formatting numbers itself. The
published text is whatever pandas made of that frame, so reproducing the frame is the
only reliable way to reproduce the text. That turns byte parity into a question about
**dtypes** — which has a finite answer — instead of a question about float repr, which
does not.

Every parity test builds its frame by running the **real** normalizer over a synthetic
source report. A hand-built frame would carry whatever dtypes the test chose, so the
export would be checked against the test's own assumptions rather than against what
the publisher writes.

## Two more corrections the harness forced

**Row order is not recoverable from the values.** NSE publishes its index report as
`Nifty 50`, `Nifty Next 50`, `Nifty 100` — source order, not alphabetical, and no sort
of the stored columns reproduces it. Equity frames happen to be sorted by symbol,
which is exactly why this would have gone unnoticed until an index file was diffed.
The store now carries `source_order`, the row's position in the published frame.

**A column's dtype is not recoverable from the values either.** The first parity run
failed on one character:

```
published: ...,352148975.0,...
exported : ...,352148975,...
```

A gapless whole-number column is `int64` in an equity frame and `float64` in an index
frame. The values are identical; nothing stored about them distinguishes the two, and
both are correct. So the store records what the publisher's frame actually was, in a
`published_frames` table of column names, dtypes and row count — one row per published
component, about 47,000 rows for a full multi-year archive. Inferring the dtype was
tried first and measured wrong, which is why it is now only the fallback for a
database written before the signature existed.

## What is proven

`tests/test_eod_export.py` publishes through the real path and regenerates:

- an equity day, byte for byte;
- a whole-number column that must not grow a decimal point, and a column with one
  missing value that must keep one — the two halves of the same rule;
- an index day in published order;
- a futures day with open interest;
- **a combined `NSE/EQ` file built from three components.** This is the hardest case:
  `pd.concat` decides the result's dtypes and an index component with no delivery
  columns widens the equity ones to float, so the exporter concatenates the way
  `CombinedFileBuilder` does rather than reading one table.

## Running it against a real tree

```
python main.py --verify-eod-parity            # every segment
python main.py --verify-eod-parity NSE_EQ     # one
```

Read-only, in the same mutually exclusive group as `--audit` and the repairs, with the
same three-valued exit: 0 clean, 1 a mismatch, 2 could not run.

The first version of this command was not actually read-only, which running it found
and reading it would not have: constructing an `EodStore` **creates** the database, so
a pass that only reports brought into existence the very thing it was asked to report
on — and on an empty data root that was the entire answer, silently replaced by an
empty success. This is the same defect `--audit` hit, so it takes the same proven
remedy: `ReadOnlyEodStore` copies the database outside the data root and opens the
copy, because SQLite's own `mode=ro` still creates and stamps the `-shm` file. The
regression test was checked by reverting the fix and confirming it fails.

Two judgements are worth naming. A file the database has no record of is reported as
**unmirrored**, not as a failure — dual-write fills forward, so files published before
it was switched on have nothing to compare with, and calling that a failure would bury
the real ones. And a report that compared nothing says so rather than printing the
success line, because an empty data root must not read as a verified one.

Which components a combined EQ file carries is decided by **row counts**, not by the
user's current append preferences: the only candidate that can be right is the one
whose component row counts sum to the lines in the file, and preferences may have
changed since it was published.

## Gates

- `pytest` both interpreters — **548 passed**, coverage 78.95% (floor 70%).
- `ruff check .` — passed. `mypy` over 60 source files — no issues.
- `main.py --smoke-gui` — exit 0. `main.py --verify-eod-parity` on an empty root —
  exit 0, and it says nothing was compared.

## Run against a real download — and the fourth correction

Three trading days (2026-08-17 to 08-19) were downloaded across all six segments with
every append option on, into the emptied data root. 25,769 rows, 18 published files,
both exchanges' combined files built. The database is 5.5 MB against 34 MB of text.

The first parity run reported **6 of 18 files mismatched**, and every one of them was
a combined EQ file:

```
published: 20MICRONS,20260817,190.54,199.4,189.0,197.26,244646,...
exported : 20MICRONS,20260817,190.54,199.4,189.0,197.26,244646.0,...
```

**A combined file is a concatenation of the components' text, not of their values.**
`DateJoinCoordinator` puts every frame through `CombinedFileBuilder.lexical_frame`
before the builder sees it, and a component reloaded from disk is read back with
`dtype=str` besides — so by the time `to_csv` runs, every column is a string and prints
exactly as its own component file printed it. Concatenating numeric frames widens
`int64` to `float64`; concatenating the text does not.

**The unit test agreed with the wrong answer**, and that is the part worth keeping.
It handed numeric frames straight to `reconcile_frames` — a path the application never
takes, because everything reaches the builder through the coordinator. A test can be
green, exercise the right function, and still not exercise the right path. It now goes
through `lexical_frame` as the application does, and reverting the fix fails it.

After the fix:

```
Checked 18 published daily file(s).

Every mirrored file regenerated byte for byte.
```

That is every segment — equity, SME, index, futures — on both exchanges, plus a
three-component NSE combined file and a two-component BSE one, regenerated from the
database alone.

## What step 2 still owes

`export_symbol` is **not** written yet. Daily files were done first because they are
self-contained; a symbol history additionally involves deduplication, the registry's
rename merge, and replayed corporate actions, and it deserves its own pass now that
the dtype and ordering questions have been answered and paid for. Nothing here is
wired into publication — that is step 3, and only after this has held for a release.

Daily files are closed: proven on real downloaded data, all six segments, both
combined shapes.
