# Code defect remediation plan

Created: 2026-08-06 (Asia/Kolkata)
Branch: `codex/pyside6-port-v1.1.0`
Source of findings: [PROJECT_REVIEW_2026-08-06.md](PROJECT_REVIEW_2026-08-06.md)
(seven adversarially-verified domain reviews; refuted findings already dropped)

Every finding below was **re-verified against this branch on 2026-08-06**, not carried
over from the review. The review ran against the older PySide6 checkout at `b7caca5`;
spot-checks confirmed the relevant code is unchanged here.

Status legend: **[ ]** not started · **[~]** in progress · **[x]** done

---

## Why this comes before the release

The v1.1.0 release is paused ([PENDING_TASKS.md](PENDING_TASKS.md) §0). Two defects
block the owner's stated goal — building a multi-year daily bhavcopy database and a
symbol-wise time-series database — and a third makes a long backfill unable to finish.
Publishing first would notify every existing v1.0.1 user to upgrade to a build that
cannot do the thing it is for.

None of these are regressions from the PySide6 port. They are pre-existing, and the
port is what made them visible.

---

## Phase 0 — Stop the test suite touching real data

**Do this before anything else.** Every phase below is verified by running the suite,
so the suite must be safe first.

### 0.1 Test isolation — effort S — **done 2026-08-06**

The exposure was real, not theoretical. `Config.__init__` resolves `~/NSE_BSE_Data`
with `Path.expanduser()` and then **mkdirs it** (`config.py:143,146`), and three test
modules construct a `Config` from the shipped `config.yaml`:
`test_gui_lifecycle.py:261,282,303`, `test_packaging_readiness.py:53`, and
`test_main_entrypoint.py:63-68` via `run_rebuild_mode`.

The review's diagnosis was confirmed exactly. `test_gui_lifecycle.py` patched
`Path.home` and *then* built `Config("config.yaml")` — but `Path.expanduser()` never
consults `Path.home`; it reads the `HOME` environment variable through
`os.path.expanduser`, so the real path was resolved regardless. Redirecting the
environment variable covers both call styles at once.

- [x] `tests/conftest.py` with an autouse fixture setting `HOME` and `USERPROFILE` to
      `tmp_path`, asserting that **both** `Path('~').expanduser()` and `Path.home()`
      resolve inside it
- [x] Per-test guard that stats the real data root and fails the individual test that
      touches it, naming the test
- [x] Session guard that fingerprints the whole tree, catching a write nested deeper
      than the root's own mtime would show. Split this way because the deep walk costs
      ~20 ms against 10,870 files — negligible once, but it would have doubled the
      suite runtime at 196 tests
- [x] Removed 13 now-redundant `Path.home` patches across five modules, and the
      imports they left unused

**Verified, not assumed.** A deliberate probe test that writes into `~/NSE_BSE_Data`
was added and run twice — once with the old per-test patches still present, once after
removing them. Both times the guard failed the test and named it:

```
Failed: tests/…::test_probe_writes_to_real_root modified the real data root
/Users/paresh/NSE_BSE_Data. Tests must never write outside tmp_path.
```

The probe also printed its resolved home as
`…/pytest-of-paresh/pytest-3763/…/home`, confirming the redirect works. The probe and
its artefact were removed; `~/NSE_BSE_Data/.state`, `/BSE` and `/NSE` remain untouched
at their original timestamps.

**No false positives:** 196 tests pass on Python 3.10 and 3.13, coverage 73.67%, Ruff
and mypy clean.

---

## Phase 1 — Unblock the historical backfill

These three are what stand between the current build and a working multi-year
database. Nothing else in this plan matters until they are done.

### 1.1 Publish EQ-only when a dependency is permanently unavailable — effort M — **done 2026-08-06**

`base_downloader.py:435-447` sets `publication_deferred` whenever any append option is
on, skipping the `.txt` write. Publication then flows only through
`DateJoinCoordinator.offer`, which returns `None` until every required component
exists. Unresolved dates reach `finalize()` → `record_failure`, which marks the stage
failed and **writes no file**. There is no EQ-only fallback.

Shipped defaults make this fire immediately: `config.yaml` has
`bse_index_append_to_eq: true`, `BSEIndexDownloader.FIRST_AVAILABLE_DATE` is
`2025-04-17` and clamps only the INDEX list, `dependencies_from_options` takes no date
parameter, and `expand_selected_exchanges` auto-adds `BSE_INDEX` whenever `BSE_EQ` is
ticked.

Effect: select BSE_EQ and backfill 2015→2026 and **no** `BSE/EQ/*.txt` file is written
for roughly ten years of dates, while those dates stay `complete=0` and are
re-downloaded on every future run forever.

The fix turned out to be one idea rather than four: **make the dependency list
date-aware**, and the rest follows.

- [x] `SEGMENT_FIRST_AVAILABLE` in `source_resolver.py` declares each segment's floor
      once, with `first_available()` and `is_available()` accessors
- [x] `BSEIndexDownloader.FIRST_AVAILABLE_DATE` now reads from it instead of holding a
      second copy of the date. Clamping only that downloader's own dates was the
      original bug — it left every earlier BSE EQ date waiting on a component the
      exchange had never produced
- [x] `DateJoinCoordinator.dependencies_for(exchange, date)` returns only the
      dependencies that exist for that date, and both `offer()` and `finalize()` use it
- [x] `finalize()` publishes when nothing is genuinely outstanding, and still records a
      failure when a dependency that *does* exist for the date failed to arrive

No `degraded` status was needed. A segment that did not exist yet is not a degraded
dependency, so the honest representation is a shorter dependency list for that date and
an ordinary successful publication. A transient failure keeps the existing
`record_failure` path, so the date stays queued for repair.

**Verified.** Four tests in `tests/test_phase7_date_join.py` cover a 2015 BSE date
publishing EQ-only, a 2025 date still waiting for INDEX, `finalize()` publishing when
nothing is outstanding, and `finalize()` still failing a genuinely missing component.
Run against the previous date-blind code, two of them fail with exactly the reported
symptom:

```
CombinedBuildResult(status='failed', output_path=None,
                    error='Required staged component did not complete: BSE_INDEX')
```

218 tests pass on Python 3.10 and 3.13, coverage 73.85%, Ruff and mypy clean,
`--smoke-gui` exits 0.

**The interim workaround is no longer needed:** `bse_index_append_to_eq` can stay on
for a historical backfill.

### 1.2 Add the missing BSE Equity era, 2023-01-01 → 2024-07-07 — effort M — **done 2026-08-06**

`source_resolver.py:92-103` maps 2022-08-17 … 2024-07-07 to a single era,
`bse-equity-bhavcopy-legacy`, requiring `SCRIP ID, SC_GROUP, TRADING_DATE`. The owner's
own prototypes prove BSE changed the schema *inside the identical filename*:

- `Bhavcopy_BSE_Eq_17_08_2022_to_31_12_2022.py` builds
  `BSE_EQ_BHAVCOPY_{ddmmyyyy}.ZIP` and parses `SCRIP ID` / `SC_GROUP` / `TRADING_DATE`
- `Bhavcopy_BSE_Eq_01_01_2023_to_31_12_2023.py` builds the **same** filename but parses
  `TckrSymb` / `SctySrs` / `TradDt`

The app cannot self-correct: `bse_eq_downloader.py:127-130` passes `era=source.era`
explicitly, so the column-sniffing fallback at `canonical_data.py:421-427` never runs
in production.

Effect: roughly 370 BSE trading days download, fail schema validation, get quarantined,
and re-enter `incomplete_dates` on every run — an infinite retry loop whose error
message names the wrong cause.

#### Sampled evidence, 2026-08-06

Real files were downloaded from BSE and inspected. The flip date is **confirmed as
2023-01-01**, and it is exact: 2022-12-31 was a Saturday and 2023-01-01 a Sunday, so
2022-12-30 and 2023-01-02 are consecutive trading days.

| Date | Header (first columns) | Schema |
|---|---|---|
| 2022-08-17 | `ISIN,SCRIP ID,SCRIP_CODE,SC_NAME,SC_GROUP,OPEN PRICE,…` | legacy |
| 2022-12-30 | `ISIN,SCRIP ID,SCRIP_CODE,SC_NAME,SC_GROUP,OPEN PRICE,…` | legacy |
| 2023-01-02 | `ISIN,TckrSymb,FinInstrmId,FinInstrmNm,SctySrs,OpnPric,…` | **UDiFF** |
| 2023-06-15 | `ISIN,TckrSymb,FinInstrmId,FinInstrmNm,SctySrs,OpnPric,…` | UDiFF |
| 2024-07-05 | `ISIN,TckrSymb,FinInstrmId,FinInstrmNm,SctySrs,OpnPric,…` | UDiFF |

Both variants carry 32 columns inside the identical
`BSE_EQ_BHAVCOPY_{ddmmyyyy}.ZIP` filename, so nothing in the URL distinguishes them.

Running the production path against the real files reproduces the failure exactly:

```
2023-01-02  app passes era='bse-equity-bhavcopy-legacy'
  DataProcessingError: report is missing required columns:
  ['CLOSING PRICE', 'HIGH PRICE', 'LOW PRICE', 'NO_OF_SHRS', 'OPEN PRICE', 'SCRIP ID', …]
```

Passing `bse-equity-udiff` to the same 2023-06-15 and 2024-07-05 files normalizes
cleanly (3,658 and 4,007 rows), which confirms the existing UDiFF mapping is reusable
as-is and only the era selection is wrong.

#### 1.2b A second, independent defect found while sampling — **done 2026-08-06**

The legacy window is broken too, for a different reason, and the review did not find
this. **Every** sampled legacy date fails:

```
2022-12-30  DataProcessingError: Normalized report contains duplicate keys: ['SYMBOL', 'SERIES']
```

BSE truncates `SCRIP ID` to 9 characters while `SC_NAME` holds the full 10. Three rows
therefore share the symbol `ICICIBANK` on 2022-12-30:

| ISIN | SCRIP ID | SC_NAME | Group | Close |
|---|---|---|---|---|
| **INE**090A01021 | `ICICIBANK` | ICICI BANK | A | ₹890.95 |
| **INF**109KC15I8 | `ICICIBANK` | ICICIBANKN | B | ₹43.15 |
| **INF**109KC1E35 | `ICICIBANK` | ICICIBANKP | B | ₹217.40 |

The `INF` prefix marks a mutual-fund unit; `INE` marks equity. The two group-B rows are
ICICI Prudential ETFs, not bank shares, and they collide with each other on
`(SYMBOL, SERIES)`.

Counts after the app's own group filter, one row per sampled date:

| Date | Rows kept | Duplicates | Duplicates if `INF` ISINs excluded |
|---|---|---|---|
| 2022-08-18 | 3,533 | 2 | **0** |
| 2022-09-15 | 3,614 | 2 | **0** |
| 2022-10-14 | 3,588 | 2 | **0** |
| 2022-11-15 | 3,629 | 2 | **0** |
| 2022-12-30 | 3,627 | 2 | **0** |
| 2023-01-02 | — | 2 | **0** |

Current behaviour is fail-closed, which is the right default — nothing is corrupted.
But it means the whole 2022-08-17 → 2024-07-05 window is unfetchable, not just the
2023+ part. That is roughly **470 trading days**, not the ~370 the review estimated.

The danger is in the obvious wrong fix. Deduplicating on `(SYMBOL, SERIES)` would let
an ETF at ₹43.15 or ₹217.40 be written into `icicibank.txt` alongside the real bank at
₹890.95, because symbol histories are keyed by symbol name. Excluding mutual-fund
units is the correct fix, and it removes every observed collision.

#### Work

- [x] Flip date pinned empirically at 2023-01-01
- [x] Added `BSE_UDIFF_SCHEMA_IN_ZIP_START` and the era `bse-equity-udiff-zip`, which
      keeps the `.ZIP` URL and reuses the existing UDiFF schema and mapping unchanged
- [x] Truncated-ticker collisions resolved by the exchange's own untruncated name
- [x] Boundary cases added to `tests/test_source_resolver.py`, collision cases to
      `tests/test_canonical_data.py`

#### The planned fix was wrong; sampling the current era corrected it

The plan said to exclude mutual-fund instruments by `INF` ISIN prefix. Checking the
**current** era first showed that would have been a mistake: 2026-07-31 keeps **194**
`INF` rows in groups A and B — `NIFTYBEES`, `SBISENSEX`, `MOM100`, `NIFTYIETF` and
similar ETFs. Excluding them historically would have produced a database with ETFs
after 2024-07-08 and none before, which is exactly the kind of silent inconsistency
this plan exists to remove.

The real cause is narrower. Ticker truncation is a property of the **old files only**:

| Era | Max ticker length | Collisions |
|---|---|---|
| 2024-07-08 onward | 11 — untruncated | none |
| 2023-01-02 (`TckrSymb`) | 9 | 2 per date |
| 2022-12-30 (`SCRIP ID`) | 9 (`SC_NAME` up to 12) | 2 per date |

Each row carries its untruncated name in the same record, and that name is unique:
0 duplicates on `(SC_NAME, group)` against 2 on `(SCRIP ID, group)`. So
`_resolve_truncated_bse_symbols` renames **only the colliding rows** to the
exchange's own name. Nothing is dropped and nothing is merged.

Note the collision is between the two ETFs, not with the bank: the bank is
`(ICICIBANK, A)` and the funds are `(ICICIBANK, B)`. Preferring the `INE` row would
not have resolved it.

#### Verified against real exchange files

All eight sampled dates now normalize, and the current era is unchanged:

```
2022-08-17  bse-equity-bhavcopy-legacy  OK 3551   ICICIBANK A 883.20  INE090A01021
                                                 ICICIBANKN B 395.69  INF109KC1E27
                                                 ICICIBANKP B 200.17  INF109KC1E35
2022-12-30  bse-equity-bhavcopy-legacy  OK 3627
2023-01-02  bse-equity-udiff-zip        OK 3782
2023-06-15  bse-equity-udiff-zip        OK 3658
2024-07-05  bse-equity-udiff-zip        OK 4007
2024-07-08  bse-equity-udiff            OK 4159
2026-07-31  bse-equity-udiff            OK 4379
```

The collision tests were run against the code with the resolver removed and failed
with the original `duplicate keys: ['SYMBOL', 'SERIES']`, so they catch the regression
rather than passing trivially.

**Observation for later, not a defect here:** BSE scrip code 542730 keeps the name
`ICICIBANKN` across 2022 but its ISIN changes from `INF109KC1E27` to `INF109KC15I8`.
The security code is stable and is preserved in `SECURITY_ID`, so identity survives,
but it confirms that ISIN alone is not a safe stable key. Relevant to the ISIN
validation work in Phase 3.3.

214 tests pass on Python 3.10 and 3.13, coverage 73.82%, Ruff and mypy clean.

### 1.3 Restore the transport retry layer — effort S — **done 2026-08-06**

Three compounding defects. All three re-verified today.

**a. The effective timeout is `total=5s` for the entire transfer.** `config.yaml:37`
defines only `timeout_seconds: 5`. The split keys `connect_timeout_seconds`,
`read_timeout_seconds` and `attempt_timeout_seconds` exist in the dataclass
(`config.py:34-36`) and are read (`config.py:175-177`) but are **absent from
config.yaml**, so `_get_timeout_budget` falls back to `total=5`. `total` covers connect
plus all body reads, and NSE FO UDiFF is a multi-MB ZIP. `tests/test_phase7_transport.py`
passes only because its fixture supplies values production never has.

**b. Every connection failure is classified as a terminal SSL error.** aiohttp's
`ClientConnectorError.__str__` is `"Cannot connect to host {host}:{port} ssl:… […]"`,
so a plain connection-refused string contains `"ssl"` and matches the SSL branch at
`async_downloader.py:338` — `should_retry: False` — before the network branch is
reached. The user is told "SSL certificate issue — server configuration problem" when
their Wi-Fi blipped, and the retry layer never runs.

**c. The circuit breaker latches open.** `transport_pool.py:109-111` raises a
`NetworkError` whose message gets the URL appended by `exceptions.py:75-81`, so
`_classify_error` substring-matches digits *inside the date in the URL*
(`20240404` → "404 not published"). `async_downloader.py:451-452` records
`success=False` when `slot()` itself raises, and a `status_code` of `None` is treated
as transient — so every date rejected during a cooldown re-arms the circuit. With
`max_concurrent_downloads: 1` the circuit can never close for the rest of the run, and
the pool is shared run-wide: **one slow date can dead-end all four NSE segments**.

- [x] Added `connect_timeout_seconds: 10`, `read_timeout_seconds: 60`,
      `attempt_timeout_seconds: 300` to `config.yaml`
- [x] `_classify_by_exception()` classifies by **type** before any message
      inspection, with SSL checked before `ClientConnectorError` because aiohttp's
      SSL errors subclass it
- [x] `CircuitOpenError(NetworkError)` is a distinct type, matched first
- [x] `record()` moved inside the slot context, so a rejection by an already-open
      circuit no longer feeds the breaker that produced it
- [x] URLs are stripped from the message before substring matching
      (`_URL_IN_MESSAGE`), so a date such as `..._20240404_...` is no longer read as
      HTTP 404
- [x] GUI timeout ceiling raised from 30 to 120, and `MAX_TIMEOUT_SECONDS` now shares
      one definition with the preference validator, which still clamped to 30 and
      would have silently reduced the user's choice

**Verified.** Eight tests added to `tests/test_phase7_transport.py`, including a
`ClientConnectorError` whose message genuinely contains `"ssl"` and a
`CircuitOpenError` whose URL genuinely contains `404`, so each test would pass
trivially if the old substring logic were still in place.

The circuit-latch test was checked against the old code by temporarily restoring the
previous `record()` placement; it failed exactly as intended
(`AssertionError: rejected dates extended the cooldown; the breaker cannot close`,
`148620.664 != 148620.663`) and passes after the fix.

204 tests pass on Python 3.10 and 3.13, coverage 73.75%, Ruff and mypy clean,
`--smoke-gui` exits 0.

---

## Phase 2 — Make a multi-year backfill finish

### 2.1 Bound and restart the history batch — effort L — **done 2026-08-06**

`symbol_history.upsert_batch` holds `history_cache` (every touched symbol's full
history) and `incoming_cache` (one `pd.Series` per row) with no eviction, and writes
only after everything is accumulated. `HistoryBatchJournal.prepare()` selects all
pending entries with **no `LIMIT`**, and `main_window.py:416-421` finalizes once after
gathering every segment — so the job **dies at the end, after hours of downloading**.

Scale: 3,318 NSE + 4,828 BSE symbol files today; 7,426 symbols/day for Select All. A
2015-today backfill is roughly 8,100 date-entries × ~7,400 rows.

#### Measured before touching anything

Peak RSS is linear in batched rows: **117 MiB + 2.03 KiB/row**, from four runs of the
real `HistoryBatchCoordinator` path against a temporary root.

| Rows batched | Peak RSS |
|---|---|
| 37,500 | 193 MiB |
| 75,000 | 266 MiB |
| 150,000 | 415 MiB |
| 300,000 | 725 MiB |

That puts the plan's own 500-date acceptance case (3.7M rows) at **~7.6 GB**, and a full
2015→2026 Select All backfill (60M rows) at ~122 GB. The review's diagnosis was right;
this quantifies it.

#### Two independent causes, not one

**A `LIMIT` on its own would not have fixed this**, which is worth stating because it is
the whole of what the plan asked for. `history_cache` holds one full history per
*touched symbol*, and every symbol trades every day, so it follows the symbol count and
history depth — not the number of dates in the batch.

Measured by batching **one date** — the smallest bucket any limit could ever cut — in a
fresh process against histories already on disk, 3,000 symbols:

| Existing history depth | Old cost of a 1-date batch | New |
|---|---|---|
| 100 rows | 84 MiB | 12 MiB |
| 300 rows | 177 MiB | 12 MiB |

The old cost grows with depth; the new one does not. Scaled to a real backfill's tail
(7,400 symbols, ~2,700 rows deep) the old figure is roughly **3.9 GB for a single
date** — with `history_batch_dates: 1`. So the limit bounds one term and the streaming
publish pass bounds the other; neither alone is enough.

- [x] `HistoryBatchJournal.prepare(limit)` cuts a batch of at most N entries, in
      `(target_date, entry_key)` order so a batch is always a contiguous window and
      rows still merge in the order they would have as one batch
- [x] `upsert_batch` now plans first and publishes second. `_plan_batch` resolves every
      row's destination from three identity columns without reading a single history
      file; the publish pass then reads, merges and writes **one symbol at a time** and
      keeps nothing after its write. This is what removes the `history_cache` term
- [x] The `iterrows` loop is gone. Planning walks plain column values, and each symbol's
      rows are gathered by position — so no `pd.Series` per row survives the batch
- [x] Per-symbol failure isolation, reported through `HistoryBatchResult.failures`
- [x] `history_batch_dates` (default 50) in `config.yaml` and `DownloadSettings`

#### What isolation actually changed

The old failure mode was worse than "aborts the whole batch". Symbols are written in
sorted order, so an abort published everything sorting *before* the bad symbol, silently
skipped everything after it, marked **every** date in the batch failed, and left the
batch open — so each later run re-attempted the same doomed symbol and stopped at the
same place. Confirmed by running probes against `5e15efc`.

Now a failed symbol is recorded with the dates its rows came from; only those dates are
marked failed, and the batch closes so the queue behind it drains. A failure that is not
per-symbol still fails closed: a rename merge (its rows live in a file the batch would
then delete) and anything past `MAX_ISOLATED_SYMBOL_FAILURES = 100`, since a full disk
fails every symbol and should not write 7,400 identical error records.

No `degraded` state was added. A held-back date is `partial`, not `failed`, because its
daily file *is* published and only the `symbols` stage is outstanding — and `partial`
already keeps `complete=0`, so the date returns through the ordinary repair path.

#### Verified

Peak RSS is now flat in batched rows, against the same coordinator path:

| Rows batched | Before | After |
|---|---|---|
| 37,500 | 193 MiB | 130 MiB |
| 150,000 | 415 MiB | 161 MiB |
| 300,000 | 725 MiB | 167 MiB |
| 600,000 | — | 180 MiB |

At Select All width (200 dates × 7,400 symbols = 1.48M rows) the knob behaves as
designed, and the cost of a smaller bucket is write volume, not correctness:

| `history_batch_dates` | Buckets | Peak RSS | Finalize |
|---|---|---|---|
| 25 | 8 | 293 MiB | 671 s |
| **50 (default)** | **4** | **366 MiB** | **315 s** |
| 100 | 2 | 474 MiB | 173 s |
| 200 (one batch) | 1 | 739 MiB | 146 s |

Note the last row: even unbucketed, 1.48M rows now peak at 739 MiB where the old code
projects ~3.1 GB. The rewrite and the limit each carry part of the fix.

#### The acceptance case, run rather than projected

500 dates × 7,400 symbols = 3.7M rows, at the shipped default, with a deliberate kill
during the third bucket:

```
offer      21.8s  peak  163 MiB
killed    108.3s  peak  322 MiB   dates already committed: 100
resume    547.8s  peak  351 MiB   buckets replayed=8
PEAK RSS 351 MiB | incomplete=0 | symbol files=7,400
```

**351 MiB against the ~7.6 GB the old code projects for the same work.** The kill
committed exactly two buckets and the resume replayed exactly the remaining eight, so
no completed bucket was re-done — the second half of the acceptance criterion, and the
reason the limit is what makes the run restartable rather than only smaller.

**Byte-equivalence was not assumed.** A generated-plan harness compared the old
unbounded implementation at `5e15efc` against the final bucketed one across 30 seeded
runs — randomized renames, mixed EQ/SME segments and deliberate volume ties, which are
the cases where a bucket boundary could change a dedup tie-break. All 30 produced
byte-identical symbol files. A second harness compared bucket sizes 1–7 against a single
unbounded batch over 150 seeds: 150/150 identical. Both harnesses were checked against a
known-different run first, so they are not passing blind.

In the suite, `test_bucketed_batches_match_one_unbounded_batch_byte_for_byte` walks
bucket sizes 1, 2, 3, 5, 7 and 12 across a rename at day 7, so at least one boundary
forces the merge to read a file an earlier bucket already wrote.

`test_batch_holds_one_symbol_history_at_a_time` pins the memory property itself: reads
and writes must interleave per symbol. Against `5e15efc` the same trace is three reads
then three writes.

Five symptom probes were run against `5e15efc` and all pass there — unbounded `prepare`,
one batch regardless of config, all-reads-before-all-writes, the mid-way abort, and one
bad symbol failing a date it never traded on — so the new tests are mirrors rather than
tests that would have passed anyway.

One test exists only because the bug nearly shipped: `DownloadSettings` is built key by
key in `config.py`, so `history_batch_dates` was present in `config.yaml` and in the
dataclass yet never forwarded — inert, but indistinguishable from working because the
default matched.
`test_history_batch_dates_reaches_the_coordinator_from_config_yaml` sets it to 17 and
follows it all the way to `HistoryBatchCoordinator.batch_dates`.

236 tests pass on Python 3.13, coverage 74.2%, Ruff and mypy clean, `--smoke-gui`
exits 0. Python 3.10 is not installed on this machine; mypy is pinned to 3.10 and
passes, and CI covers the runtime.

**Still true after this change:** each bucket rewrites every touched symbol file in
full, and `_write_history` copies a backup on every write (2.2), so the write volume
above is roughly double what it needs to be. 2.2 halves it; Phase 5 removes the
rewrite-per-bucket cost entirely.

*(2.2 is now done and the halving was measured — see below. The rewrite-per-bucket
cost remains, and is Phase 5's.)*

### 2.2 Drop the per-write symbol backup — effort S — **done 2026-08-06**

`symbol_history.py:353-372` (the review's `277-286`, moved by 2.1) wrote a full backup
copy on **every** symbol write. Grepping for readers of that tree finds exactly one
hit: the writer. It is never read.

- [x] Removed, not made opt-in
- [x] Retention for `.state/quarantine`, `.state/raw_revisions` and the legacy
      `.state/backups` tree an older build left behind

#### Removed rather than made opt-in

The plan allowed either. Three properties settle it, and the third is the one that
changes the answer:

1. Nothing reads it. The only hit is the writer, so there is no restore path — a user
   would have to copy files back by hand.
2. It is one generation deep.
3. **After 2.1 it is not even a pre-run snapshot.** A batch rewrites each touched
   symbol once per *bucket*, so at the end of a 500-date backfill the copy holds
   whatever the previous bucket wrote — an arbitrary mid-run point. Restoring from it
   would produce a file that is neither the state before the run nor after it.

An opt-in flag would therefore have shipped a knob that turns on a second copy nothing
can restore from. The recoverable source of truth is `.state/raw`: checksummed, keyed
by date, and what `--rebuild-symbol/-exchange/-all` actually read. A test pins that a
rebuild still works after a sweep.

#### Measured, not projected

Every byte the history stage writes, counted by wrapping `to_csv` and `shutil.copy2`
around the real `upsert_batch` path against a temporary root:

| Case | Before | After |
|---|---|---|
| Steady state: 500-date history on disk, one new date, 300 symbols | 13.56 MiB | **6.81 MiB** |
| Cold backfill: 200 dates × 300 symbols, `history_batch_dates: 50` | 15.41 MiB | **11.29 MiB** |

The steady-state row is the exact halving 2.1 predicted: the copy is the same size as
the file replacing it, so it is 50.0% of the stage's write bytes. The cold-backfill row
is 27%, and the difference is not noise — the copy holds the *previous*, shorter
history, so a from-scratch run backs up less than it writes. The steady state is the
one that runs every day for years.

(The residual 0.03 MiB after the change is `VersionedJSONStore` writing a `.bak` of
`symbol_registry.json` once per batch, which is a different mechanism and is read on
recovery.)

#### Retention

Three trees under `.state` exist only for after-the-fact diagnosis and are read by
nothing: `quarantine`, `raw_revisions`, and `backups`. `state_retention.py` prunes
them once per run, from `state_retention` in `config.yaml`:

| Tree | Kept |
|---|---|
| `quarantine` | 30 days, newest 100 **per category** |
| `raw_revisions` | 90 days, newest 5 **per exchange/segment/date** |
| `backups` | nothing — the tree an older build left behind is drained |

The count budgets are per group rather than global, so a burst of quarantined BSE
source reports cannot evict the history corruption record that explains a failed run.
A negative value disables a rule, so `legacy_backup_days: -1` keeps the old tree.

`.state/raw` is deliberately not on the list, and `backups` is drained rather than
aged because nothing writes it any more — leaving it would strand a stale copy that
looks restorable and is not.

#### Verified

`test_symbol_writes_no_longer_copy_a_backup` fails against the previous code with
`assert not (tmp_path/.state/backups).exists()`, so it is a mirror of the defect rather
than a test that would have passed anyway.

Most of the retention suite pins what the sweep must **not** do, because it deletes
files inside the user's data root: `.state/raw`, the live state documents and
`components/` survive a sweep; a symlink is neither followed nor removed (the file it
points at outside the tree is untouched); an undeletable file is reported as an error
rather than raised; a policy that raises is contained to its own tree; and a retention
failure cannot fail a run. `test_shipped_config_yaml_reaches_the_retention_policies`
follows a value from `config.yaml` to the policy — 2.1 shipped an inert key once, and
that is the shape of test that would have caught it.

256 tests pass on Python 3.13, coverage 75.3% (`state_retention.py` at 100%), Ruff and
mypy clean, `--smoke-gui` exits 0. Python 3.10 is not installed on this machine; mypy
is pinned to 3.10 and passes, and CI covers the runtime.

**On upgrade:** the first run after this build deletes `.state/backups`. On this
machine that tree is 130 files / 1.4 MB; the release plan measured 8,250 files / 36 MB
on a fuller one. No file in it is referenced by anything.

### 2.3 Historical holiday calendar — effort M — **done 2026-08-06**

Every pre-2025 exchange holiday currently becomes a permanent "failed" date, because an
empty API year is treated as success.

- [x] Bundle a static holiday table for past years
- [x] Treat an empty API year as failure, not success
- [x] *Added:* retire a date the exchange never published, so a holiday the calendar
      cannot cover stops returning — see "the premise was half right" below

#### The premise was half right, and the half that was wrong changes the fix

Sampled the official API directly rather than assuming:

| Year | `CM` records |
|---|---|
| 1990, 1995, 2000, 2005, 2010, 2027 | response is `{}` — no `CM` key at all |
| 2013 … 2026 | 17–22 real holidays every year |

So the API **does** serve history, back to 2013. "Pre-2025" was wrong; the real floor is
**2013**, and it is the API's floor, not a choice. The `{}` response also does not take
the "empty year" branch — with no `CM` key `fetch_holidays_for_year` already raised and
returned `None`.

The *consequence* the plan described is real, but it arrives by a different route: a year
that cannot be fetched — no calendar, NSE blocking the request, a timeout — leaves
`is_holiday` answering `False` for every date in it, which is indistinguishable from a
real answer. The holiday is queued as a trading day, its download 404s, the date is left
at `complete=0` with no skip reason, and `incomplete_dates` returns it on **every**
future run forever.

That is why this took three parts rather than two. Bundling the calendar fixes 2013
onward. Nothing fixes 1996–2012, because no source has that data. Only making the
exchange's own 404 conclusive stops those dates coming back.

#### 1. The bundled calendar

`src/utils/holiday_calendar.py` holds 2013–2026 exactly as the API reported them,
generated by `tools/generate_holiday_calendar.py` and regenerable with one command. It
is a Python module rather than a data file so the packaged build carries it with no
change to the Nuitka data-file list.

Three layers now answer, in order of authority: the live API, the cache it fills, and
the bundle. The bundle is applied **after** the cache-write decision, so the on-disk
cache never claims a year it did not fetch — a cache that lies about coverage would hide
exactly the failure this section is about.

#### 2. An empty calendar is a failure

`fetch_holidays_for_year` now returns `None` when the parsed calendar is empty. No NSE
year has ever had zero capital-market holidays, so "empty" can only mean "no data".
A year no source can answer is logged as such instead of silently reading as "no
holidays", and `refresh_holidays()` reports whether the *live* refresh succeeded — with
a bundle always present, `bool(holidays)` could no longer report failure at all.

#### 3. A 404 the exchange means is now conclusive

`AbsentReportLedger` in `base_downloader.py`. A price download that comes back
`file_not_found` for a date at least `ABSENT_REPORT_SETTLE_DAYS` (3) old becomes a
*candidate*, and at the end of the segment the candidates are retired with
`skip_date("The exchange published no report for this date")`, which sets `complete=1`
and takes them out of `incomplete_dates`.

**A 404 on its own proves nothing**, and the guard is the important part: a candidate is
retired only if the same segment successfully downloaded some **other date in the same
year** during that run. A wrong URL era 404s its whole window, downloads nothing, and so
retires nothing — it stays loud. That is not hypothetical; it fired on real data the
first time it ran (below).

Unsettled candidates keep their failed stage and are reported as *"the source itself may
be wrong"*, so a date the run could not explain is queued for repair rather than quietly
dropped.

#### Verified against the exchange, not only in tests

**The bundle matches what NSE actually published.** Every bundled weekday holiday was
requested from the archive:

| Year | Bundled weekday holidays | That nonetheless published a bhavcopy | Control weekdays sampled | Unexpected non-200 |
|---|---|---|---|---|
| 2015 | 14 | **0** | 6 | **0** |
| 2023 | 15 | **0** | 6 | **0** |

**The 404 signal is exact.** Every weekday of January 2010 — a year with no calendar
from any source — was requested: 19 published, 2 did not, and the two are 2010-01-01 and
2010-01-26, New Year's Day and Republic Day.

**A real run over a real holiday.** NSE EQ, 2010-01-25 to 2010-01-27, against a
temporary data root:

```
2010-01-25 Mon  partial   file published
2010-01-26 Tue  skipped   The exchange published no report for this date
2010-01-27 Wed  partial   file published
incomplete_dates = [2010-01-25, 2010-01-27]      the holiday is gone from the queue
```

The two trading days are `partial` for reasons this section does not own: NSE published
no delivery report in 2010 (that is 2.4), and the harness ran without a history
coordinator. The holiday is the line that matters, and it is `skipped`.

**The guard fired on its own.** The same run against NSE **INDEX** retired *nothing*:

```
NSE_INDEX: 3 date(s) returned 'not found' and no other date in that year
downloaded, so the source itself may be wrong; they remain queued for repair
```

That is correct — NSE has no index archive for 2010 at all, including 2010-03-02, an
ordinary Tuesday. Had the guard not existed, two genuine trading days would have been
retired as "not published" and lost silently.

**A third cross-check, unplanned.** While probing segment floors, 2018-03-02 answered
404 for INDEX, FO *and* SME. It is in the bundled calendar: Holi.

#### Why 1996–2012 is not bundled

The archive does go back that far — 1996-01-02 downloads, 1994-01-03 does not — so the
gap is real. Deriving those years would mean probing every weekday, roughly 4,250
requests across 17 years, to avoid roughly 255 one-time 404s that part 3 already retires
after seeing each once. That is the wrong trade, and it would put more load on the
exchange than the backfill it is meant to help.

**Known limitation, deliberate:** a retirement is not durable. `PipelineManifest.begin`
clears `skipped_reason` so a run that deliberately re-selects a date can complete it, and
a user re-running the *same* range therefore re-probes those dates once per run. What is
fixed is the accumulating case — dates returning through `incomplete_dates` on every run
regardless of the selected range, which is what made every run report errors forever.

**Observation for 4.2, not a defect here:** NSE INDEX and SME have no 2010 archive while
NSE EQ and FO do. `SEGMENT_FIRST_AVAILABLE` currently bounds only BSE INDEX, so the
picker still offers dates no segment can serve. That is 4.2's per-segment floor.

270 tests pass on Python 3.13, coverage 75.7%, Ruff and mypy clean. The retirement test
was run against the pre-2.3 code and fails there with `assert 'failed' == 'skipped'`, so
it mirrors the defect rather than passing anyway.

### 2.4 Bound the delivery-retry queue — effort M — **done 2026-08-06**

Dates whose delivery report will never exist are re-downloaded forever.

- [x] Cap and expire pending-delivery retries (`delivery_state.py:50-82`)
- [x] Add an era branch (or `None`) for NSE delivery so an absent source marks the
      stage `disabled` and the segment can report success

#### Both floors pinned, both between consecutive trading days

The plan named NSE. Sampling found BSE has the same shape with a different date:

| Exchange | Report | Last 404 | First 200 |
|---|---|---|---|
| NSE | `sec_bhavdata_full_DDMMYYYY.csv` | 2019-09-27 (Fri) | **2019-09-30 (Mon)** |
| BSE | `SCBSEALL{ddmm}.zip` | every trading day of Dec 2005 | **2006-01-02** |

Both boundaries are exact: 2019-09-28/29 were the weekend, and the BSE archive starts
with its `/gross/{year}/` directory. Every sampled month from 2005 to 2019-09 is absent
for NSE, and 2006 through 2024 is present for BSE.

The price archives go back much further — NSE equity to 1996 — so the gap is not a
rounding error. It is **nine years** of NSE dates whose delivery report cannot exist.

#### The loop had two mouths, and closing one only moves it

`PendingDeliveryStore` had no bound of any kind: `add` accumulated and `discard` only
ever ran on success. But the pipeline manifest holds the same date a second time — a
failed `delivery` stage leaves `complete=0`, so `incomplete_dates` re-queues it as well.
Dropping the pending entry alone would have left the manifest queue running, and
disabling the stage alone would have left the pending queue running.

So `_retire_absent_delivery` releases both together, before the day list is built, and
the date leaves both queues in the same run.

#### What each part does

- `DELIVERY_FIRST_AVAILABLE` in `source_resolver.py`, with `delivery_available()`
  alongside the `is_available()` that 1.1 added for segments.
- `_pipeline_requirements(target_date)` is now date-aware: below the floor `delivery`
  goes into *disabled* rather than *required*, so `begin()` records it that way on every
  run and no separate call is needed to keep it disabled.
- The download loop no longer requests a delivery report it knows does not exist.
- `PendingDeliveryStore` is version 2, keyed by date to `{first_seen, attempts}`, and
  `expire()` retires an entry that is below the floor, older than
  `MAX_PENDING_DELIVERY_DAYS` (30), or past `MAX_DELIVERY_ATTEMPTS` (20). The age bound
  is the real one; the attempt bound only matters to someone who runs the application
  many times a day, where a month of waiting would be absurd.
- The version 1 migration sets `first_seen` to the **trading date**, not today, so an
  entry carried over from an older build retires on the first run instead of receiving a
  fresh 30-day lease.

#### The segment could not report success even once every stage was settled

`_core_pipeline_ok` read *segment-level* requirements and asked whether `delivery` was in
each date's completed stages. A stage the date itself disabled is never "complete", so a
date whose delivery is retired would have held the whole segment below success — the
exact thing the plan's second bullet asks for. `DateResult` now carries
`disabled_stages`, `_core_pipeline_ok` subtracts them, and it reads per-date
requirements rather than one segment-wide set.

#### Verified against the exchange

NSE EQ, 2010-01-25 to 2010-01-27, real network, temporary data root:

```
segment core-ok = True
  2010-01-25 Mon  done=(daily, downloaded, validated)  disabled=(combined, delivery)
  2010-01-26 Tue  skipped   The exchange published no report for this date   [2.3]
  2010-01-27 Wed  done=(daily, downloaded, validated)  disabled=(combined, delivery)
  pending_delivery = no file
```

The same run before this change left both trading days waiting on a delivery report that
has never existed, and wrote them into `pending_delivery.json` to be asked for again on
every future run. Now the file is never created: the dates below the floor are not
requested at all.

Thirteen tests, of which eight fail against the pre-2.4 code when the floor and the
expiry are reverted, so they mirror the defect. The two that matter most are
`test_an_expired_pending_date_is_released_by_both_queues` — which drives the sweep with
*no* dates selected, so only the retirement can clear it — and
`test_a_date_above_the_floor_still_requests_and_queues_delivery`, because bounding a
retry queue must not quietly disable the late-report handling the queue exists for.

283 tests pass on Python 3.13, coverage 76.0%, Ruff and mypy clean, `--smoke-gui`
exits 0.

**On upgrade:** `pending_delivery.json` migrates from version 1 to 2 on first read. On
this machine it is version 1 and empty, so nothing is retired.

---

## Phase 3 — Stop writing wrong data

### 3.1 Corporate-action parser — effort M — **done 2026-08-06**

`SPLIT_PATTERN` (`corporate_actions.py:29`) is an unanchored `search`. Executed against
the real function during review:

```
'Sub-Division w.e.f. 12-08-2024 From Rs.10/- To Re.1/-'  -> ('split', 1.5)
                                        parsed 12 and 08 from the DATE; true factor 10.0
'Bonus 1:1 and Face Value Split From Rs 10 To Rs 2'      -> ('bonus', 2.0)
                                        discards the 5:1 split entirely
```

BSE descriptions concatenate `Purpose` + `Detail/Remarks`, which is exactly where
free-text dates live.

**Honest qualification:** the continuity guard at `corporate_actions.py:502-512` blocks
most damage — it compares pre- and post-ex closes and routes to `manual_review` with no
write outside a 0.67–1.50 ratio. The 10:1-parsed-as-1.5 case is caught. What escapes is
the narrow band (a true 2.0 parsed as 1.5 gives 0.75) and any run where the ex-date bar
is not yet downloaded, so the guard is skipped. The dominant failure mode is therefore a
**stalled `manual_review` entry with no GUI workflow to resolve it**.

#### What the exchanges actually publish, 2006–2026

Both feeds were pulled in full rather than reasoned about: 35,091 NSE `subject` records
(2010–2026) and 33,278 BSE `Purpose` rows (2006–2026), **7,996 distinct descriptions**.
That corrects two of the six planned bullets and confirms the most damaging one.

| Planned premise | What the corpus shows |
|---|---|
| Free-text dates inside descriptions | **0 of 7,996** carry a date-shaped substring; **0** contain `w.e.f.` |
| BSE `Detail/Remarks` is where dates live | The CSV endpoint the app uses has no such column at all, so the concatenation in `normalize_bse_actions` has always appended an empty string |
| Require `from X to Y` and face-value tokens | Both are optional in the real wordings — `Fv Split Rs.10 To Re.1` has no "from"; `Face Value Split From 10/- To Face Value 2/-` has no "Rs" |
| Return a *list* of actions | **Confirmed and the big one.** 30 distinct NSE subjects name a bonus *and* a split; only the bonus was ever produced |
| More than two surviving numbers → review | Only 6 descriptions in twenty years are action-shaped and unreadable, and all 6 are genuinely unusable |

Date stripping was implemented anyway — it costs one regex and one stray date would be
read as a ratio — but it is inert on every real description, which was measured rather
than assumed.

#### The rule the corpus actually needs

The real defect is not the date; it is that the search was unanchored, so it read the
first two numbers *anywhere* in the string. Descriptions routinely open with an unrelated
rupee amount:

```
'Interim Dividend Rs 2/- Per Share And Face Value Split From Rs 10/- To Rs 2/-'
   old -> ('split', 0.2)      read the dividend, then the 10;  true factor 5
'Annual General Meeting / Dividend - Rs 1.35/- ... Split - From Rs 10/- To Rs 2/-'
   old -> ('split', 0.135)                                     true factor 5
```

So `SPLIT_PATTERN` now searches only **after** the split keyword, and the pair must be
`X … to … Y` with the direction matching the word used — a "split" that raises the face
value, or a "consolidation" that lowers it, is refused rather than guessed. Anchoring on
the keyword also reads `Capital Reduction Rs 10 To Rs 3.30 / Consolidation Rs 3.30 To
Rs.10` correctly (0.33, from the consolidation's own numbers) where the old whole-string
search returned 3.03.

#### Verified against the exchange's own prices, not only against tests

If a description names both a bonus and a split, the market's ex-date gap is the product
of the two. Nine announcements were checked by downloading the NSE bhavcopy either side
of the ex-date and dividing the closes:

| Symbol | Ex-date | Previous close | Ex-date close | Actual gap | Old factor | New factor |
|---|---|---|---|---|---|---|
| SUNILHITEC | 2016-12-01 | 202.75 | 10.65 | **19.04** | 2 | **20** |
| SBC | 2022-02-22 | 147.50 | 7.75 | **19.03** | 2 | **20** |
| VINNY | 2023-02-24 | 334.30 | 15.25 | **21.92** | 2.3 | **23** |
| MMTC | 2010-07-29 | 30222.85 | 1841.80 | **16.41** | 2 | **20** |
| BAJFINANCE | 2016-09-08 | 11393.30 | 1162.80 | **9.80** | 2 | **10** |
| SHARONBIO | 2014-02-20 | 411.85 | 49.40 | **8.34** | 2 | **10** |
| EASEMYTRIP | 2022-11-21 | 381.95 | 57.30 | **6.67** | 4 | **8** |
| AARTECH | 2024-08-09 | 212.64 | 74.37 | **2.86** | 1.5 | **3** |
| AVANTIFEED | 2018-06-26 | 1563.35 | 599.30 | **2.61** | 1.5 | **3** |

Every gap matches the composed factor and none matches the bonus alone. Across the whole
corpus the new parser changes **38** of 7,996 descriptions, produces an action for **no**
description that previously produced none, and drops none that previously parsed — so it
is a correction, not a widening.

#### Work

- [x] Date-shaped substrings stripped before matching (inert on real data, kept as
      insurance)
- [x] `parse_actions()` returns a **list**, so one announcement can carry both a bonus
      and a face-value split; the engine's existing per-(symbol, ex-date) grouping then
      composes them — `bonus 1:1 × split 10→1 = 20`
- [x] The face-value pair is read only after the split keyword, with the direction
      checked against the word used
- [x] Nothing is guessed: an action-shaped description that cannot be read produces no
      action and a stated reason
- [x] `factor` dropped from the audit key, with a ledger migration (below)
- [x] A 51-case table of verbatim NSE and BSE strings in `tests/test_corporate_actions.py`

#### Dropping the factor from the key needed a ledger migration, not just an edit

The plan's reasoning was right and the consequence is larger than one line. Keys are the
ledger's primary keys, so re-deriving them without the factor makes **every existing
record unfindable** — and an unfindable applied action is re-applied. Run against the
pre-3.1 code, with a backfill bucket that stops the day before the ex-date so the
continuity guard cannot run:

```
start                       CLOSE = 202.75
after the bonus-only read   CLOSE = 101.40      (the truth is 10.14)
after the corrected read    CLOSE =   5.05      divided by 2, then by 20
```

So the ledger is now version 3 and `migrate_corporate_ledger` re-derives every key from
the record's own fields, including the keys inside each transaction's `action_keys` and
`final_action_records`. Transaction ids are deliberately left alone: a prepared
transaction's staged CSV is named after its id, and renaming would orphan that file.

Dry-run against a copy of the live ledger on this machine — 149 records, 134
transactions: version 2 → 3, every announcement survives, every key is the new
derivation, every status unchanged, every transaction key resolves to a live record.

An already-applied action whose factor this run reads differently is now **reported, not
re-applied** (`summary["factor_conflicts"]`, surfaced in the GUI). Re-dividing is the
real damage; only a `--rebuild-symbol` can adopt a corrected factor, and the ledger note
says so.

#### The unchecked path is closed too

The guard is skipped whenever the history has no bar on or after the ex-date — which a
50-date backfill bucket produces every time it stops the day before one. An action in
that position is now recorded `awaiting_ex_date` and left for the next run, where
`reconcile_pending` (already called at the end of every `upsert_batch`) retries it with
the guard active. Like the two existing retry statuses it is unbounded; the cost is one
local file read per run, not a download.

**What is still open, deliberately:** the six unreadable descriptions are dropped
silently. There is nowhere to report them — no logging configuration exists anywhere in
the application (4.3). The GUI now reports deferred actions and factor conflicts because
those flow through the apply summary; parse failures do not, and plumbing them is 4.3's
work, not this section's.

**On upgrade:** `corporate_actions.json` migrates from version 2 to 3 on first read. On
this machine that is 149 records and 134 transactions, all preserved. Re-reading all 149
descriptions with the new parser produces **0 factor conflicts and 0 dropped records**,
so nothing on this machine's data changes. A rollback to an older build would refuse to
read a v3 ledger (`StateCorruptionError`) rather than silently re-apply — the same
property v1 → v2 already had.

346 tests pass on Python 3.10 and 3.13, coverage 76.2%, Ruff and mypy clean,
`--smoke-gui` exits 0. Every new behavioural test was run against the code with its own
fix reverted and fails there: the deferral test reports `applied=1` instead of
`deferred=1`, the key tests fail on the hash, and the three migration tests fail on
`assert 2 == 3`.

### 3.2 Inverse-adjust volume — effort S

`corporate_actions.py:495-500` loops only `("OPEN","HIGH","LOW","CLOSE")`. After a 1:10
split the price series is continuous while volume steps 10×, poisoning every RVOL and
turnover screen across the whole history.

- [ ] Inverse-adjust `VOLUME`, `DELIVERY_QTY`, `QTY_PER_TRADE`
- [ ] Do **not** scale `TOTAL_TRADES` — it is a transaction count
- [ ] Stop tick-snapping adjusted prices (`corporate_actions.py:498-500`). Adjusted
      prices are synthetic and have no reason to sit on a 0.05 grid; snapping injects
      ±0.025 per bar and compounds across successive actions
- [ ] Ship with a `.state` version marker and a rebuild prompt

### 3.3 Protect symbol identity — effort S

- [ ] Validate ISIN format before it can act as a stable key
- [ ] Refuse to merge histories with overlapping dates
- [ ] Move superseded files to `.state/quarantine/merged/` instead of `unlink()`
      (`symbol_history.py:322-343` — the `unlink()` is unconditional today)
- [ ] Add ticker-reuse protection: `_ensure_symbol_filename` keys on the ticker string
      alone, so a delisted ticker reassigned to a new company appends into the old file
      and `_deduplicate` then destroys one row per shared date.

      **A second, sharper symptom, found while doing 2.1 and confirmed pre-existing.**
      When the rename and the reuse fall inside the *same* batch, the reused ticker's
      rows are not merged — they are dropped and the file is deleted. `AAA` (ISIN X)
      renames to `AAA-NEW`, retiring `aaa.txt`; a different company (ISIN Y) then lists
      under the freed ticker `AAA`, and its rows are assigned to `aaa.txt`, which is
      still in the batch's retired set. Retired paths are excluded from the write set
      and unlinked at the end, so the day-3 row is silently lost:

      ```
      files: ['aaa-new.txt']   # aaa.txt deleted, its day-3 row gone
      day-3 row survived: False
      ```

      Verified byte-identical on `5e15efc` and on the current branch, so 2.1 neither
      caused nor changed it. Fixing it needs the identity model this section is about,
      not another special case in the batch, which is why it is recorded here.
- [ ] Add an ISIN or `SECURITY_ID` column to `SYMBOL_HISTORY_COLUMNS` so a file is
      self-identifying and merge errors are reversible after the fact

### 3.4 Row-count and value gates — effort S

`validate_canonical_data` requires only `not frame.empty`, and `validate_daily_output`
parses only the first and last lines. A 5-row placeholder bhavcopy is accepted as a
complete trading day and never re-downloaded.

- [ ] Per-(exchange, segment) row-count band from the trailing-20-session median;
      reject and re-queue outside 50–150%
- [ ] Make `validate_daily_output` count lines
- [ ] Reject all-zero OHLC rows and add an `LOW <= min(O,C) <= max(O,C) <= HIGH` check —
      currently only NaN and negative are rejected, so `0,0,0,0,0` publishes and reads
      downstream as a genuine −100% day
- [ ] Prefer the *newer* row in `_deduplicate`, not the higher-volume one. Exchanges
      republish corrected bhavcopies and corrections frequently *reduce* volume, so the
      corrected row is currently discarded in favour of the erroneous one
- [ ] Explicit `lineterminator="\n"` on `to_csv` — output is currently `os.linesep`, so
      a cloud-synced or cross-platform data root produces byte-different files and
      breaks the sha256-based corporate-action ledger

### 3.5 Single-instance lock — effort M

Two concurrent copies silently destroy symbol histories and the registry.

- [ ] `fcntl.flock` on `<base>/.state/app.lock`, keyed on the resolved data root

---

## Phase 4 — Make the database verifiable

### 4.1 A read-only `--audit` command — effort M

sha256 digests are written into the manifest and **never read back**. The only checksum
verification in the codebase is for raw snapshots and the update downloader.
`get_missing_file_dates` computes expected dates only *between* the first and last
existing filename, so a truncated head or stale tail is invisible. Nothing enumerates
`<EX>/SYMBOLS/` at all. The only repair tooling is the destructive `--rebuild-*`.

For a database intended to accumulate over years and be traded off, "is it complete and
self-consistent?" is currently unanswerable without mutating it.

- [ ] Verify every manifest digest against disk
- [ ] Per-segment expected-row-count bands
- [ ] Head/tail gap detection against `base_start_date`, not the first filename
- [ ] `SYMBOLS/*.txt` date coverage cross-checked against the checksummed `.state/raw`
      snapshots
- [ ] Orphan detection
- [ ] Read-only: the command must never write

### 4.2 Missing fields and metadata — effort M

- [ ] `TURNOVER` and `PREV_CLOSE`. Grepping all of `src/` for
      `TOTTRDVAL|TtlTrfVal|NET_TURNOV|PREVCLOSE|PrvsClsgPric` returns **zero hits**. The
      prototypes prove the source files carry them. These are the only genuinely
      unrecoverable fields — everything else is already in `.state/raw` at full
      fidelity and can be re-published offline.
- [ ] A header row, a schema version, and a per-segment manifest. Files are headerless
      and `validate_daily_output` accepts *either* 7 or 9 columns, so two schema
      generations coexist as "valid" with no marker.
- [ ] A per-segment earliest-available floor, so "how far back can I go?" is answerable
      from the UI. Today the picker offers 1990 and
      `price_source('NSE','SME',date(1995,1,1))` returns a URL.
- [ ] Delivery match-rate telemetry — the stage is marked complete on HTTP success,
      before the join.
- [ ] Add `'TS'` to `BSE_EQUITY_SERIES`. BSE Startup scrips are silently absent from
      every era. Settle the naming decision (PENDING_TASKS §1) **before** release;
      changing it later forces a migration of published files.

### 4.3 Diagnostics — effort S

- [ ] `RotatingFileHandler` in `run_gui_mode` and a Help → "Open Log Folder" action.
      There is no logging configuration anywhere; a packaged binary produces zero
      diagnostics.

---

## Phase 5 — Storage model

### 5.1 SQLite as system of record, text files as a regenerable export — effort L

Everything is CSV on both axes: daily files and one text file per symbol. Any change to
one symbol costs a full read, validate, sort, dedup, rewrite, plus a full backup copy.
Appending one trading day rewrites the entire symbol database and a second copy of it.

This is the direct cause of the memory ceiling, the doubled write volume, the redundant
hashing, and the fact that `rebuild_exchange` will not finish at 3,318 symbols.

Proposed target, from §7 of the review: SQLite with a `(symbol, date)` primary key as
the system of record; the existing text files regenerated from it on demand, so the
owner's downstream consumers (`mark_screener`, `opentrader313`) keep reading exactly
what they read today.

**Do not start this until Phases 1–3 are done.** Phase 2.1 buys enough headroom to
finish a backfill; this is the durable fix, and it should be built against a codebase
whose correctness defects are already closed.

---

## Sequencing

```
Phase 0  test isolation          ← done; everything else is verified by running the suite
   ↓
Phase 1  backfill blockers       ← done; 1.1, 1.2, 1.3
   ↓
Phase 2  make it finish          ← done.  2.1 memory ceiling gone; 2.2 write volume
                                   halved and .state bounded; 2.3 holidays known
                                   offline and absent reports retire; 2.4 delivery
                                   retries bounded
   ↓
Phase 3  stop writing wrong data ← 3.1 done; 3.2-3.5 open. Some items need a
                                   rebuild prompt
   ↓
Phase 4  verifiability           ← --audit answers "can I trust this?"
   ↓
Phase 5  storage model           ← the durable fix, built on a correct base
```

Phases 1 and 3 can proceed in parallel if convenient — they touch disjoint files.
Phase 2.1 and Phase 5 must not be worked simultaneously; 5 supersedes much of 2.1.

## Relationship to the release

The release (PENDING_TASKS §0) resumes when **Phase 1 is complete and Phase 2.1 is at
least bounded**. That is the point at which a user who upgrades can actually build the
database the application promises. Phases 3–5 can ship in 1.1.1 and later.

**That condition is now met, and then some** (2026-08-06): Phases 1 and 2 are complete.
1.1, 1.2 and 1.3 unblock the backfill; 2.1 went past "bounded" to flat; and 2.2, 2.3 and
2.4 remove the three ways a long run used to leave permanent work behind — a doubled
write volume, holidays that failed forever, and delivery reports retried forever.

Resuming the release is a separate decision and this note does not make it. Phases 3–5
are still open and can ship in 1.1.1 and later; the release notes now also need to
mention that the first run clears `.state/backups` (2.2).

2.2 is also done, which matters to the release for a second reason: the first run
after upgrading deletes `.state/backups`, so that behaviour belongs in the release
notes rather than arriving unannounced.
