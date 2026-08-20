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

### 3.2 Inverse-adjust volume — effort S — **done 2026-08-07**

`corporate_actions.py:495-500` loops only `("OPEN","HIGH","LOW","CLOSE")`. After a 1:10
split the price series is continuous while volume steps 10×, poisoning every RVOL and
turnover screen across the whole history.

- [x] Adjust `VOLUME`, `DELIVERY_QTY`, `QTY_PER_TRADE`
- [x] Do **not** scale `TOTAL_TRADES` — it is a transaction count
- [x] Stop tick-snapping adjusted prices
- [x] Ship with a `.state` version marker and a rebuild prompt

#### The operation is multiply, not divide

"Inverse-adjust" is ambiguous and the wrong reading destroys the series. Factors are
greater than 1 for splits and bonuses and prices are *divided* by them, so share counts
must be **multiplied**. Dividing would double the discontinuity rather than remove it.

The invariant that settles it is turnover: price × volume is money that changed hands,
and it cannot jump because the unit of a share changed. MWL's real NSE bars either side
of its 10:1 split on 2026-07-10, run through the shipped engine:

| Date | CLOSE before | CLOSE after | VOLUME before | VOLUME after |
|---|---|---|---|---|
| 2026-07-08 | 373.10 | 37.31 | 62,105 | 621,050 |
| 2026-07-09 | 370.25 | 37.02 | 120,588 | 1,205,880 |
| **2026-07-10 (ex)** | 36.65 | 36.65 | 7,549,418 | 7,549,418 |

Before the change the pre-split turnover computed as 37.70 × 29,403 = ₹1.11M where the
market actually traded ₹11.08M that day — the adjustment had silently divided ten years
of turnover by the split factor. `QTY_PER_TRADE` is `VOLUME / TOTAL_TRADES` at source
(`canonical_data.py:595`), so scaling the volume without the trade count requires
scaling the rate by the same factor to keep that identity true. `DELIVERY_PERCENT` is a
ratio of two columns that both scale, so it is left alone.

#### The documented reason for the old rule was checked, not assumed

`IMPLEMENTATION_PLAN.md:98-101` pins **both** halves of the old behaviour — the 0.05
snap and the untouched volume — to "`Mark Python` compatibility". That is the owner's
own downstream screener, so it was read rather than reasoned about:

- `Mark Python/config.py:47-50` puts its database at `~/Mark EMA and HTF/Database/
  NSE_BSE_EOD.duckdb`, filled by its own `nse_eod_downloader`/`bse_eod_downloader`.
- A search of that tree for `NSE_BSE_Data` returns **nothing**. It never reads these
  files.
- `Mark Python/eod_store.py:16` stores `Date, Symbol, Exchange, Open, High, Low, Close,
  Volume` only, and `nse_eod_downloader/eod_downloader.py:157` runs the same OHLC-only,
  0.05-snapped adjustment on its own copy.

So nothing breaks. What changes is that the two databases now disagree about volume
across a corporate action — this one is right, and Mark Python has the same defect in
its own code.

#### Tick snapping can zero a real price

Measured over all 2,720 real NSE closes on 2026-07-31, comparing the snapped quotient
against the exact one:

| Factor | Median error | p95 | Max | Rows over 0.5% |
|---|---|---|---|---|
| 2 | 0.010% | 0.33% | 33.3% | 92 |
| 5 | 0.025% | 0.88% | 66.7% | 229 |
| 10 | 0.048% | 1.63% | **100%** | 375 |

The 100% is not a rounding artefact. A stock whose LOW is ₹0.14 divided by 10 gives
0.014, which snaps to **0.00**. On that one day a 10× adjustment zeroes 3 real prices
and a 20× adjustment zeroes 11 — a zero OHLC value reads downstream as a −100% bar, the
exact failure 3.4 exists to reject. Two successive actions compound it: median error
0.114% for a 10 then a 2.

Snapping is gone; adjusted prices are rounded to two decimals, which is what the writers
emit anyway. The tick size itself was never configurable — it was a constructor default
no caller ever passed — so the parameter is removed rather than exposed.

#### One arithmetic, not two copies of it

`symbol_history._apply_recorded_actions` held a **second** implementation of the rule,
with the tick size hardcoded as a `0.05` literal, used whenever a raw row is
re-downloaded or a rebuild replays a snapshot. Both now call one `adjust_rows()`.

That coupling is the important part, because a disagreement between them is not an error
anywhere: `_deduplicate` keeps the **higher-volume** row per date
(`symbol_history.py:305-312`), so whichever path scaled the volume simply wins and the
history drifts in silence. The direction that bites is a consolidation, where the
adjusted volume is *smaller* than the raw one and an unscaled replay would outrank and
quietly undo the adjustment. Both cases are pinned by tests.

Group iteration is now sorted, so two ex-dates arriving in one run compose in the same
chronological order the replay uses. Rounding is not associative, so the order was
observable.

#### The marker, and what it can honestly promise

An audited action is never applied twice, so corrected arithmetic cannot reach bars that
were already adjusted — only a rebuild can. `.state/history_revision.json` records the
adjustment revision **per exchange**; `--rebuild-exchange` and `--rebuild-all` set it to
current, and the GUI and CLI report which exchanges are behind.

A fresh installation has no symbol files and is deliberately never prompted. The prompt
also states its own limit: a rebuild replays `.state/raw`, so it can only repair dates
this application downloaded.

On this machine both exchanges are behind, and the raw tree covers the whole span the
histories do, so a rebuild would repair all of it:

```
stale exchanges: ['BSE', 'NSE']
NSE: 3,354 symbol files, 144 raw snapshot dates (2026-01-01 .. 2026-08-04)
BSE: 4,931 symbol files, 144 raw snapshot dates (2026-01-01 .. 2026-08-04)
```

`README.md` carried the old contract in three places ("Volume, delivery fields and FO OI
are not adjusted") and now states the new one, including that pre-1.1.0 histories keep
the old arithmetic until rebuilt.

#### An adversarial review found four defects in the first cut

The change above was reviewed by independent agents on three lenses (arithmetic and
dtypes, interaction with existing machinery, the marker itself), each finding put to
three skeptics. Seven findings survived; four were defects in this work and were fixed
before it is called done. Every one was reproduced by hand before being accepted.

**1. A consolidation rewrote thinly-traded days as untraded.** `(values * factor).round()`
with a factor below 1 sends a small share count to zero:

```
before   20250101, CLOSE 1.0, VOLUME 4, TOTAL_TRADES 2, DELIVERY_QTY 4, DLV% 100
after    20250101, CLOSE 10.0, VOLUME 0, TOTAL_TRADES 2, DELIVERY_QTY 0, DLV% 100
```

Turnover ₹4 → ₹0 — a 100% error in the exact quantity this section exists to preserve —
and the row asserts zero shares traded in two trades at 100% delivery. Nothing catches
it: `_validate_history` checks only that OHLC parse, and the continuity guard reads only
CLOSE. On the owner's own BSE tree the 1st-percentile daily volume is **2 shares**, and a
sample of 600 files (74,772 traded days) had **1,269 days zeroed** at a 10:1
consolidation. No whole number is right here — 4 shares at 10:1 is 0.4 — but "it traded"
is a fact, so a positive count now floors at 1. Re-measured on the same sample: 0 zeroed,
2,123 floored (2.84%).

**2. `_deduplicate` let a stale row outrank its own repair.** It kept the
**higher-volume** row per date. That was invisible while volume was never adjusted — the
two rows always tied and position decided — but scaling volume turned the tiebreak into a
value comparison. On an install upgraded from 1.1.0 the stored bar has the old rule's raw
volume while a re-download is replayed under the new one, so for a consolidation the
stale row always wins and a delivery repair is discarded with `failures=()` and no error
anywhere. This is **3.4's "prefer the newer row" item, pulled forward**, because 3.2 is
what made it harmful: the later row now wins, which is also the right answer for the case
3.4 raised (exchanges republish corrected bhavcopies and corrections frequently *reduce*
volume).

**3. A fresh install was told its histories were stale.** `mark_current` had exactly one
caller — `rebuild_exchange` — so the moment a new user's first day was published,
`stale_exchanges()` reported the exchange as revision 1 and the notice repeated at every
startup, every refresh and after every download, forever. The commit's claim that a fresh
install is never prompted held only for a data root with nothing downloaded yet. The
write path now stamps the current revision on an exchange whose history *starts* under
it, and leaves an exchange that already has files alone.

**4. The prompt asked for a repair that could not run.** `rebuild_exchange` raises when
`.state/raw/<EX>` is empty and `rebuild_all` derives its exchange list from the snapshots,
while `stale_exchanges` derives its list from `SYMBOLS/*.txt`. An exchange in one list and
not the other was stuck: the rebuild failed, the notice reprinted unchanged, forever. The
notice now names those exchanges separately and says a rebuild cannot repair them.

**Two findings were left, deliberately.** An adjusted price can still round to `0.00` for
a stock under ₹0.005 × factor — but the strongest refutation measured the owner's real
tree (1,057,347 closes, 147 applied actions) and found the closest real action was 70×
clear of the threshold, while this change already *improves* the count 26× over the tick
snap it replaced (1,618 → 61 closes at factor 10). Rejecting zero OHLC is 3.4's item and
belongs there. Separately, `rebuild_exchange` can revert an adjustment for a symbol whose
`stable_id` fell back to its ticker and was later renamed — reproduced at `3bdf198^` as
well, so it is pre-existing, and it is an identity defect that belongs to **3.3**.

362 tests pass on Python 3.10 and 3.13, coverage 76.5%, Ruff and mypy clean,
`--smoke-gui` exits 0. Each new test was run against the code with its own fix reverted
and fails there — including **two that first passed for the wrong reason**: the
byte-equality version of the replay test was satisfied by `_deduplicate` discarding the
unscaled row, and the first dedup test built its stored row with the *current* build, so
both rows tied and the old rule passed it too. Both were rewritten until they failed.

### 3.3 Protect symbol identity — effort S

**Done 2026-08-08.** Notes below the checklist.

- [x] Validate ISIN format before it can act as a stable key
- [x] Refuse to merge histories with overlapping dates
- [x] Move superseded files to `.state/quarantine/merged/` instead of `unlink()`
      (`symbol_history.py:322-343` — the `unlink()` is unconditional today)
- [x] Add ticker-reuse protection: `_ensure_symbol_filename` keys on the ticker string
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
- [x] Add an ISIN or `SECURITY_ID` column to `SYMBOL_HISTORY_COLUMNS` so a file is
      self-identifying and merge errors are reversible after the fact

#### How 3.3 was closed

**Identifier shapes were measured before they were enforced.** Across the owner's
full tree — 1,057,349 raw rows — every non-blank ISIN has the ISO 6166 shape
(`[A-Z]{2}[A-Z0-9]{9}[0-9]`) and every `SECURITY_ID` is purely numeric, so the
validators reject nothing real. They are applied at every point a value becomes a
key: `_stable_keys_from` (price rows), both action normalizers (a malformed
identifier falls back to the ticker exactly as a missing one does, so two garbage
announcements can no longer collide on one ledger key), and the registry migration,
which prunes malformed keys a pre-validation build let in. 64,392 rows (NSE SME)
carry no identifier at all and keep working by name.

**The registry is now version 3 and each filename remembers its owner.** A new
`identities` section maps `filename -> stable keys`; `_ensure_symbol_filename`
refuses a name whose recorded identity is disjoint from the row's and assigns a
digest-suffixed file instead. The entry outlives the file, so a name retired by a
rename inside the same batch cannot be handed to a different security either — the
plan's `aaa.txt` scenario now publishes the day-3 row to `aaa--<digest>.txt` with
zero rows lost, verified by test. Migration from v2 seeds identities by inverting
the existing key->symbol mappings through the filename table, so previously
written files are protected immediately. A relisting company whose keys match
reclaims its original file. Known trade-off: an NSE security whose ISIN is
*reassigned* forks into a new file at the change (BSE is bridged by the stable
numeric code); a fork is visible and mergeable, silent destruction was neither.

**A merge is refused when both stored histories claim the same dates.** A genuine
rename retires one ticker before the next trades, so overlap means two securities
holding one identifier. Both files and both mappings are kept, the stable key
follows the row that carries it today (so the refusal does not repeat every
batch), and the refusal is surfaced: `HistoryBatchResult.refused_merges`, the
`history_batch_finished` telemetry event, and a GUI error naming both files.
Deliberately *not* refused: overlap between a stored file and the batch's own
incoming rows — that is the ordinary re-download/repair path, which deduplication
resolves by design; refusing it would break every delivery repair. The residual
case — two companies erroneously sharing a key with **zero days** between the
error and the collision, inside one batch — still deduplicates; the cross-batch
form, which is what an erroneous feed actually produces, is refused.

**Superseded files move to `.state/quarantine/merged/`** (named
`<stem>.<sha12>.txt`), in both the incremental and the batch path. After a good
merge the copy is redundant — the rows live in the merged file — so the existing
quarantine retention (30 days, 100 files per category) applies. If the move
fails, the file is left in place: stale and visible beats deleted.

**Symbol files are self-identifying.** `ISIN` is appended as the twelfth column —
trailing, so positional readers of the first eleven keep working — written only
when it has the identifier's shape and blank otherwise (SME). Legacy 11-column
files upgrade on read and are rewritten in the new schema on their next write; a
corporate-action transaction staged by a pre-ISIN build recovers after a crash by
replaying through the current schema and adopting the digest of the bytes it
actually published (the stage's own bytes are checksum-verified first).

**The recorded rebuild defect is fixed at both call sites.** The action replay now
matches every name the security is known by: the rebuild passes the closure's
symbol set, and the live paths pass the row's name plus the tickers its
identifiers resolved to in the registry *as it stood before the batch re-pointed
it*. A ticker-fallback `stable_id` therefore survives the rename in both
directions — action recorded under the old name replayed onto a renamed history,
and action recorded under the new name replayed onto a re-downloaded pre-rename
row. The rebuild closure also stopped following ticker names blindly: a row that
carries identifiers joins only through them (identifier-less rows still join by
name), and the closure seeds from the requested name's file identity, so
rebuilding a renamed security no longer swallows the history of a different
company that later took over its old ticker.

16 new tests (378 total), and every one was run against the code with its own fix
reverted and fails there — nine targeted reverts in all. Existing fixtures used
abbreviated fake ISINs (`INE1`); all were upgraded to shape-valid values.
Both interpreters: pytest green, coverage 76.8% (3.13), Ruff and mypy clean,
`--smoke-gui` exits 0.

### 3.4 Row-count and value gates — effort S

**Done 2026-08-08.** Notes below the checklist.

`validate_canonical_data` requires only `not frame.empty`, and `validate_daily_output`
parses only the first and last lines. A 5-row placeholder bhavcopy is accepted as a
complete trading day and never re-downloaded.

- [x] Per-(exchange, segment) row-count band from the trailing-20-session median;
      reject and re-queue outside 50–150%
- [x] Make `validate_daily_output` count lines
- [x] Reject all-zero OHLC rows and add an `LOW <= min(O,C) <= max(O,C) <= HIGH` check —
      currently only NaN and negative are rejected, so `0,0,0,0,0` publishes and reads
      downstream as a genuine −100% day
- [x] Prefer the *newer* row in `_deduplicate`, not the higher-volume one. Exchanges
      republish corrected bhavcopies and corrections frequently *reduce* volume, so the
      corrected row is currently discarded in favour of the erroneous one.
      **Done in 3.2**, which is what made it urgent: once a corporate action scaled
      volume, the higher-volume rank became a value comparison a stale row could win, so
      a delivery repair was silently discarded on any upgraded install
- [x] Explicit `lineterminator="\n"` on `to_csv` — output is currently `os.linesep`, so
      a cloud-synced or cross-platform data root produces byte-different files and
      breaks the sha256-based corporate-action ledger

#### How 3.4 was closed

**The planned OHLC check was wrong as written, and sampling is what showed it.**
`LOW <= min(O,C) <= max(O,C) <= HIGH` applied literally rejects **every single NSE
FO day**: 1,515 of 91,870 real futures rows carry `0,0,0` OHL with a real
settlement CLOSE, because a far-month contract that did not trade still has a
settlement price. Rejecting a whole day for that would have blocked the entire FO
segment forever. Two further real rows fail even the refined rule — BSE publishes
`NCC#` (2026-01-27) and `IRFC#` (2026-02-23) with a settlement close above the
day's high on a one-share trade — and 58 NSE FO rows do the same, because a thin
contract's settlement price is modelled rather than traded.

So the rule that shipped is: judge a row only where a **traded range exists**
(HIGH and LOW both above zero), read a zero OPEN/CLOSE inside such a row as
*absent* rather than as a price of zero, and fail the day only when violations
exceed `max(5 rows, 5%)`. The worst real day measured is 0.47% (3 of 640 NSE FO
rows) while a misaligned or shifted source file violates in nearly every row, so
the threshold separates the two cases with an order of magnitude to spare.

**All-zero rows are dropped, not failed.** A row with `0,0,0,0` carries no price
at all and reads downstream as a −100% bar. It never occurs in the owner's tree
(0 of 1,057,349 equity, 91,870 FO and 31,874 index rows), but older bhavcopy eras
are not yet downloaded, so the row is dropped with a logged count instead of
failing a day that is otherwise fine. A report where *every* row is unpriced is
refused outright.

**The band compares against the nearest sessions, not strictly earlier ones.**
"Trailing-20" assumes a forward-only run; a backfill walks *backwards*, so for an
older date the only comparable sessions are the ones just fetched after it. The
query takes the nearest completed sessions in either direction, capped at 180
days so a 1995 session is never judged against a 2026 one — a market that grew
2.7× in between would fail the band on era alone. The check runs before anything
is written, so a rejected date is marked failed and returns through the ordinary
repair path. A manifest that cannot answer never blocks a download.

**The read side counts lines against what the writer recorded.** `rows` is
already in the manifest for every published date, and the combined stage is
authoritative where it runs (the file on disk is the one *it* wrote). Verified
against the real tree: recorded counts match the bytes on disk for all 864 files,
6 of 6 segments, zero mismatches. A data root written by an older build has no
recorded expectation, so nothing is invented for it and the structural checks
stand alone.

**Whole-tree replay.** The shipped gate functions were run over every one of the
864 real published day-files: **0 days rejected, 0 rows dropped**. The band was
replayed the way a download would see it, per segment, over all 144 sessions each
— also 0 rejections.

`lineterminator="\n"` is now explicit at all nine `to_csv` call sites; the two
tests that pin it patch `os.linesep` to `\r\n` and fail without the fix.

### 3.5 Single-instance lock — effort M

**Done 2026-08-08.**

Two concurrent copies silently destroy symbol histories and the registry.

- [x] `fcntl.flock` on `<base>/.state/app.lock`, keyed on the resolved data root

`SingleInstanceLock` holds an advisory whole-file lock for the lifetime of the
process. The lock file lives *inside* the data root, so two copies configured
with different roots never contend and two paths reaching one root through a
symlink share one inode and therefore one lock. The holder writes its pid, host
and start time into the file, so the second copy's message names what is holding
it rather than saying only "in use"; the note is cleared on release, and a hard
kill still frees the lock because the OS drops it with the file descriptor.

Both entry points take it: the GUI (with a message box, since a second copy is
usually started by double-clicking) and `--rebuild-*`, which rewrites the very
histories a running download appends to. A filesystem that cannot lock at all —
some network shares — logs a warning and continues, because failing there would
break a setup that works today; only a genuine `EACCES`/`EAGAIN` means "held".

---

## Phase 4 — Make the database verifiable

### 4.1 A read-only `--audit` command — effort M — **done 2026-08-20**

sha256 digests were written into the manifest and **never read back**. The only checksum
verification in the codebase was for raw snapshots and the update downloader.
`get_missing_file_dates` computes expected dates only *between* the first and last
existing filename, so a truncated head or stale tail was invisible. Nothing enumerated
`<EX>/SYMBOLS/` at all. The only repair tooling was the destructive `--rebuild-*`.

For a database intended to accumulate over years and be traded off, "is it complete and
self-consistent?" was unanswerable without mutating it.

- [x] Verify every manifest digest against disk
- [x] Per-segment expected-row-count bands
- [x] Head/tail gap detection against `base_start_date`, not the first filename
- [x] `SYMBOLS/*.txt` date coverage cross-checked against the checksummed `.state/raw`
      snapshots
- [x] Orphan detection
- [x] Read-only: the command must never write

Implemented in `src/services/audit_service.py`, driven by `python main.py --audit
[EXCHANGE_SEGMENT ...]`. Exit codes are three-valued — `0` clean, `1` findings, `2` the
audit could not look — because a script that treated the last two alike would report a
broken audit as clean data.

#### Read-only had to be built, not merely intended

Four ordinary routes into a data root write before they read, so none of them could be
used: `Config.get_data_path` creates the folder it is asked about (`resolve_data_path`
was split out of it), `DataManager.__init__` creates the whole folder structure (the
filename contract moved to `DAILY_FILE_PATTERNS` at module scope), `PipelineManifest`
initialises its SQLite schema and imports the legacy JSON in its constructor, and
`VersionedJSONStore.read` copies anything it cannot parse into `.state/quarantine`. A
record this command cannot parse is reported, not moved.

SQLite's own `mode=ro` is not read-only either, which measuring found and reading would
not have: opening the real data root that way left `pipeline_state.sqlite3-shm`
rewritten, because a WAL database needs its shared-memory index and a read-only
*connection* still creates and stamps it. On a genuinely read-only medium it would fail
outright. `ReadOnlyPipelineStore` copies the database to a temporary directory outside
the data root and opens the copy.

`tests/test_audit_command.py` fingerprints every path, mtime, size and content digest
under a data root around a full run. Reverting the SQLite copy to `mode=ro` fails it.

#### The false positive that would have made it useless

For NSE EQ the daily stage records the *component's* digest under `sha256`, and the
combined stage appends SME and Index rows to the published file afterwards, recording
its own digest. Comparing the daily digest against that file would have reported every
combined date as corrupt. The component and the combined output are each checked against
their own digest instead; both halves are pinned by tests.

The same distinction governs row counts: `_recorded_rows` prefers the combined stage's
count exactly as `published_row_counts` does, or a deferred EQ file would look 600 rows
short of its record every day.

#### Coverage is judged offline, and declines what it cannot know

The calendar comes from the saved holiday cache first and the bundled 2013–2026 calendar
second. `HolidayManager.is_holiday` fetches a year it does not have, and an audit that
hangs trying to reach NSE cannot help diagnose the TLS failure that withdrew v1.1.0.
`HolidayManager.cached_calendar` was added for this. A year neither source covers is
declined rather than guessed — assuming it had no holidays would report every Diwali in
it as a missing session — and the report names those years.

A stale tail is a notice rather than a warning. Freshness is the owner's decision, and a
command that failed because he had not downloaded today is one he would stop running.

#### Symbol coverage without loading the whole database

Coverage is held as one bit per snapshot date. A finished multi-year backfill holds tens
of millions of (symbol, date) pairs; a bitmask keeps a 4,000-symbol, 7,500-session
expectation inside a few megabytes. A raw row is resolved to its file by identity first
and ticker second, the way the store itself does it — resolving by ticker alone would
look for the file a rename had already merged away, and report a healthy database as
missing thousands of histories.

#### Measured on the owner's real data root, 2026-08-20

`~/NSE_BSE_Data`, 8,615 entries:

- 308 recorded digests verified against disk across six segments, all matching;
- 84 raw snapshots verified against their own metadata, all matching;
- 8,096 symbol histories reconciled against those snapshots — 208,555 (symbol, date)
  pairs — with no gap in either direction;
- 12 findings, all true: six head gaps of 234 trading days each (the database starts
  2026-07-01, `base_start_date` is 2025-07-15) and six stale tails of 8 sessions;
- 2.8 seconds for the whole run;
- the tree byte-for-byte and stat-for-stat unchanged afterwards.

Two defects were found by running it rather than by reading it: the SQLite `-shm` write
above, and `with_suffix` being unable to undo a two-part suffix, which turned
`2026-07-01.csv.meta.json` into `2026-07-01.csv.csv` and reported all 84 healthy
snapshots as widowed.

#### What it does not do

The per-segment earliest-available floor belongs to 4.2, so the head-gap check measures
against `base_start_date` for every segment. Until 4.2 lands, a segment the exchange
simply did not publish that far back will report a head gap that no download can close.

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

#### Sampled evidence, 2026-08-20

One real report was downloaded from every supported era before any code was written,
because the grep in the box above searches for the wrong names and therefore makes this
look simpler than it is. Six of the columns that actually carry these fields are not in
that pattern at all.

| Era | Turnover column | Unit | Previous-close column |
| --- | --- | --- | --- |
| `nse-equity-legacy` | `TOTTRDVAL` | rupees | `PREVCLOSE` |
| `nse-equity-udiff` | `TtlTrfVal` | rupees | `PrvsClsgPric` |
| `nse-fo-legacy` | `VAL_INLAKH` | **lakhs** | **absent** |
| `nse-fo-udiff` | `TtlTrfVal` | rupees | `PrvsClsgPric` |
| `nse-sme-two-digit-year` | `NET_TRDVAL` | rupees | `PREV_CL_PR` |
| `nse-sme-four-digit-year` | `NET_TRDVAL` | rupees | `PREV_CL_PR` |
| `nse-index` | `Turnover (Rs. Cr.)` | **crores** | **absent** |
| `bse-equity-isin-legacy` | `NET_TURNOV` | rupees | `PREVCLOSE` |
| `bse-equity-bhavcopy-legacy` | `NET_TURNOV` | rupees | `PREVIOUS CLOSE PRICE` |
| `bse-equity-udiff-zip` | `TtlTrfVal` | rupees | `PrvsClsgPric` |
| `bse-equity-udiff` | `TtlTrfVal` | rupees | `PrvsClsgPric` |
| `bse-index` | **absent** | — | `PreviousClose` |
| `nse-delivery` | `TURNOVER_LACS` | **lakhs** | `PREV_CLOSE` |
| `bse-delivery` | `DAY'S TURNOVER` | rupees | absent |

Units were decided by arithmetic rather than by the column's name, because a name is a
claim and three different units are in play:

* every equity era gives `turnover / (volume * close)` a median of **1.000**, over
  2,775 to 4,939 rows each — rupees, including BSE's `NET_TURNOV`;
* NSE F&O cannot be checked that way, since no lot size is in the file. Read as lakhs,
  2024-07-05 totals Rs 13,626,112 crore; read as rupees, Rs 136 crore. The next
  schema's `TtlTrfVal` totals Rs 11,979,608 crore for 2026-08-07. Lakhs it is, and the
  boundary at 2024-07-08 is a five-order-of-magnitude discontinuity if missed;
* an index value is not a price per share, so the index turnover was checked against
  the whole cash market instead: "Nifty Total Market" read as crores is 83.8% of that
  day's NSE turnover, and read as lakhs 0.84%;
* BSE's `DAY'S TURNOVER` matches the same day's bhavcopy `TtlTrfVal` with a median
  ratio of 1.0000 across 4,938 scrips.

Two corrections to what the box above assumes:

* the 2022-08-17 to 2022-12-31 BSE era names it `PREVIOUS CLOSE PRICE`, with spaces,
  not `PREVCLOSE`;
* **the NSE index previous close cannot be derived.** `Closing - Points Change` agrees
  with the previous session's own close to the paisa for 162 of 163 indices, but NSE
  publishes `Points Change = 0.0` and `Change(%) = "-"` for *Nifty50 Dividend Points*,
  where the derivation gives 187.24 against an actual 182.42. The other six
  disagreements are 0.01 rounding. A rule that is silently wrong for one index in a
  hundred and sixty is not a rule worth having, so this field stays empty.

NSE equity's `PrvsClsgPric` was confirmed to be the raw previous close, agreeing with
the previous session's `ClsPric` for 3,325 of 3,325 instruments.

#### Decided output contract

* Both fields are published to daily files **and** symbol histories.
* Stored in **rupees** everywhere: NSE F&O legacy is multiplied by 100,000 and NSE index
  by 10,000,000, so one column never holds three units.
* Where the exchange published nothing, the field is **empty**, not zero. Zero would
  claim a BSE index has no turnover rather than that none is published.
* Daily files stay **headerless**; a per-segment schema manifest records the column
  names and generation instead, so the two schema generations become distinguishable
  without breaking any consumer that reads the files positionally.
* In symbol histories the new columns go **after** `ISIN`, so no column that has
  already been written moves.
* `.state/raw` snapshots gain both columns, and `read_internal_snapshot` must accept
  the previous column set as well — it validates the list exactly and quarantines what
  it cannot match, so a strict change would send every existing snapshot to quarantine
  the first time a rebuild ran.

### 4.3 Diagnostics — effort S — **done 2026-08-19**

- [x] `RotatingFileHandler` and a Help → "Open Log Folder" action. There was no
      logging configuration anywhere, so a packaged binary produced zero
      diagnostics: every `logger.info` went to a handler that was never
      installed, and only Python's last-resort handler put warnings on a console
      a windowed application does not have.

Implemented in `src/utils/logging_setup.py`, configured from `main()` so the
repair commands are covered too, not only the GUI. Logs go to
`~/.nse_bse_downloader/logs/`, bounded at 2 MB with five backups, and never
under the data root. The console keeps `WARNING` and above; the file keeps
`INFO`, so a terminal stays readable while the detail survives.

Every run starts by recording the version, build date, platform, Python version,
whether this is a packaged build, and which certificate bundle it loaded — so a
log file attached to an issue identifies itself without anyone having to ask.
That last line is the one whose absence withdrew v1.1.0.

The cost of not having this was measured: diagnosing that certificate defect
meant running the packaged binary from a terminal to capture stderr, which is
not something a user can be asked to do.

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
whose correctness defects are already closed. **That precondition was met on
2026-08-08**, when 3.5 closed Phase 3.

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
Phase 3  stop writing wrong data ← done.  3.1 parser, 3.2 volume, 3.3 identity,
                                   3.4 size/value gates, 3.5 one writer per root
   ↓
Phase 4  verifiability           ← 4.1 done: --audit answers "can I trust
                                   this?" without changing the answer.  4.2
                                   missing fields still open; 4.3 done
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
