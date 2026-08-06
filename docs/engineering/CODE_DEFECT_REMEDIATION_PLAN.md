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

### 1.1 Publish EQ-only when a dependency is permanently unavailable — effort M — **blocker**

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

- [ ] Make `dependencies_from_options` date-aware so BSE INDEX is not required before
      `2025-04-17` (`combined_file_builder.py:269-288`)
- [ ] In `DateJoinCoordinator.finalize()`, publish EQ-only via
      `CombinedFileBuilder.reconcile(exchange, date, ())` when a dependency is
      *permanently* unavailable, and mark the combined stage `degraded`/`disabled`
      rather than `failed` (`date_join_coordinator.py:97-119`)
- [ ] Wire `reconcile()` into a post-run pass; today it exists only behind the manual
      `--rebuild-combined` flag
- [ ] Distinguish *permanently* unavailable (before first-available date, hard 404 on a
      historical date) from *transiently* unavailable (timeout, 5xx) — only the former
      degrades, the latter must still retry

**Interim workaround for the owner, usable today:** untick `bse_index_append_to_eq`
before any BSE backfill.

**Acceptance:** a backfill of BSE_EQ over a 2015 date range produces one `.txt` per
trading day; those dates report `complete=1`; a second run downloads nothing.

### 1.2 Add the missing BSE Equity era, 2023-01-01 → 2024-07-07 — effort M — **blocker**

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

- [ ] **Pin the flip date empirically first.** Sample 2022-12-30 and 2023-01-02 and
      inspect the actual columns. 2023-01-01 is inferred from prototype filenames, not
      observed. Record the sampled evidence in this file.
- [ ] Add `BSE_UDIFF_SCHEMA_IN_ZIP_START` and a fourth era `bse-equity-udiff-zip` that
      keeps the `.ZIP` URL but reuses the UDiFF schema and mapping
- [ ] Add parametrised cases at the two boundary dates to
      `tests/test_source_resolver.py` and `tests/test_canonical_data.py`

**Acceptance:** a backfill across 2022-12-15 → 2023-01-15 publishes every trading day
with no quarantine.

### 1.3 Restore the transport retry layer — effort S — **blocker**

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

- [ ] Add `connect_timeout_seconds: 10`, `read_timeout_seconds: 60`,
      `attempt_timeout_seconds: 300` to `config.yaml`
- [ ] Classify transport errors by **exception type**, not by substring matching on
      the message (`async_downloader.py:317-413`)
- [ ] Give the breaker a typed `CircuitOpenError`, handled before any string matching
- [ ] Move `record()` inside the slot context so only real request outcomes feed it
- [ ] Strip the URL from classifier input
- [ ] Raise the GUI timeout spinbox cap above 30 (`main_window.py:1377-1378`)

**Acceptance:** a unit test asserting a `ClientConnectorError` for connection-refused
classifies as retryable network, not SSL; a test asserting a circuit-open rejection
does not itself re-arm the circuit; a real multi-MB FO download completes.

---

## Phase 2 — Make a multi-year backfill finish

### 2.1 Bound and restart the history batch — effort L

`symbol_history.upsert_batch` holds `history_cache` (every touched symbol's full
history) and `incoming_cache` (one `pd.Series` per row) with no eviction, and writes
only after everything is accumulated. `HistoryBatchJournal.prepare()` selects all
pending entries with **no `LIMIT`**, and `main_window.py:416-421` finalizes once after
gathering every segment — so the job **dies at the end, after hours of downloading**.

Scale: 3,318 NSE + 4,828 BSE symbol files today; 7,426 symbols/day for Select All. A
2015-today backfill is roughly 8,100 date-entries × ~7,400 rows.

- [ ] Flush every N dates instead of once at the end
- [ ] Evict `history_cache` / `incoming_cache` per flushed bucket
- [ ] Add a `LIMIT` to `HistoryBatchJournal.prepare()`
- [ ] Replace the `iterrows` loop with `groupby("SYMBOL")`
- [ ] Per-symbol failure isolation — one bad row currently aborts the whole batch

**Acceptance:** a 500-date backfill completes with bounded peak RSS, and killing the
process mid-run resumes without re-doing completed buckets.

### 2.2 Drop the per-write symbol backup — effort S

`symbol_history.py:277-286` writes a full backup copy on **every** symbol write.
Grepping for readers of that tree finds exactly one hit: the writer. It is never read.
This doubles history-stage write I/O for no benefit.

- [ ] Remove it, or make it opt-in
- [ ] Add retention for `.state/raw_revisions` and `.state/quarantine`

### 2.3 Historical holiday calendar — effort M

Every pre-2025 exchange holiday currently becomes a permanent "failed" date, because an
empty API year is treated as success.

- [ ] Bundle a static holiday table for past years
- [ ] Treat an empty API year as failure, not success

### 2.4 Bound the delivery-retry queue — effort M

Dates whose delivery report will never exist are re-downloaded forever.

- [ ] Cap and expire pending-delivery retries (`delivery_state.py:50-82`)
- [ ] Add an era branch (or `None`) for NSE delivery so an absent source marks the
      stage `disabled` and the segment can report success

---

## Phase 3 — Stop writing wrong data

### 3.1 Corporate-action parser — effort M

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

- [ ] Strip date-shaped substrings before matching
- [ ] Require `from X to Y` ordering and face-value tokens
- [ ] Return a *list* of actions per description
- [ ] Route more than two surviving numbers to `manual_review`
- [ ] Drop `factor` from `CorporateAction.key` (`corporate_actions.py:66-71`) — it
      currently hashes the parsed factor, so any future parser fix re-keys historical
      announcements and re-divides already-adjusted files
- [ ] Build a ~40-case unit table from real NSE `subject` and BSE `Purpose` strings

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
      and `_deduplicate` then destroys one row per shared date
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
Phase 0  test isolation          ← everything else is verified by running the suite
   ↓
Phase 1  backfill blockers       ← 1.1, 1.2, 1.3 — the owner's goal is blocked without these
   ↓
Phase 2  make it finish          ← 2.1 is the hard ceiling on any multi-year run
   ↓
Phase 3  stop writing wrong data ← correctness; some items need a rebuild prompt
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
