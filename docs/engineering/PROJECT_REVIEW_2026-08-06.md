All key claims independently verified against the code. Writing the final report.

# NSE/BSE Downloader — Final Review

**Scope:** `/Users/paresh/Documents/New project/NSE_BSE_Downloader 1.0.1 pyside6`, read-only. Seven adversarially-verified domain reviews consolidated; refuted findings dropped; corrected severities applied. I independently re-verified the five most load-bearing claims by reading and executing code — those are marked **[verified by me]**.

---

## 1. OVERALL VERDICT

The **plumbing is genuinely good and the data is not there yet**. You have built a real pipeline — one central era-aware URL resolver, per-era schema contracts that fail closed, atomic publication, SQLite/WAL stage state with working resume, a batched symbol-history writer with a crash-resumable journal, and a two-phase-commit corporate-action engine. That is well above prototype quality and well above what the parent-directory scripts did.

But **neither of your two target databases can currently be completed**. A backfill of BSE Equity dies on an unmapped 18-month schema era (2023-01-01 → 2024-07-05); with the shipped default options, BSE EQ produces *no daily file at all* for any date before 2025-04-17; and a multi-year symbol-history batch materialises the whole run in RAM. Roughly **60% of the daily-bhavcopy database and ~45% of the symbol-wise database** is built as machinery; as *acquired, trustworthy data* the number is far lower, because `config.yaml:64` still ships `base_start_date: 2025-07-15` and the historical backfill has never survived a full run.

The honest summary: **you are about six well-scoped fixes away from being able to run the backfill you actually want**, and about two architectural decisions away from that backfill being sustainable.

---

## 2. PROGRESS SCORECARD

| Capability | Status | Evidence |
|---|---|---|
| **Legacy URL discovery & era cutovers** | **Partial** | One central resolver returns `(url, era, kind)` for 6 segments + 2 delivery reports (`source_resolver.py:29-139`); every downloader calls it (`bse_eq_downloader.py:68`, `nse_eq_downloader.py:40`). 4 cutovers implemented, all exclusive-safe (`source_resolver.py:14-17`). **Missing: the BSE 2023 schema flip**, any era branch for NSE delivery (`source_resolver.py:121-131`), and any earliest-available floor except BSE INDEX (`bse_index_downloader.py:19`). |
| **Daily bhavcopy acquisition** | **Partial** | Works for NSE EQ/FO/SME/INDEX and BSE UDiFF. Blocked for BSE EQ 2023-01-01→2024-07-05 (schema error), and blocked for *all* BSE EQ before 2025-04-17 by the combined-file dependency trap (§4.1). Transport caps every request at `total=5s` (`config.yaml:37` + `async_downloader.py:243-258`). |
| **Canonical normalization** | **Done** | 13 era schemas with required-column sets that fail closed (`canonical_data.py:49-146`, `199-229`); HTML/JSON/empty/bad-ZIP payloads rejected (`canonical_data.py:149-185`); NaN/negative/duplicate-key rejection (`canonical_data.py:284-306`); float64 preserved (`memory_optimizer.py:194-198`). |
| **Delivery data** | **Partial** | NSE `sec_bhavdata_full` and BSE `SCBSEALL` wired (`source_resolver.py:121-138`). No era branch, so pre-`sec_bhavdata_full` dates 404 forever. Stage marked `complete` on HTTP success, **before** the join runs (`base_downloader.py:678-691`); `merge_delivery` counts nothing (`canonical_data.py:511-548`). |
| **FO / Open Interest** | **Partial** | Futures only. Options are dropped at the parser: `{"STF","IDF"}` for UDiFF (`canonical_data.py:575`) and `{"FUTSTK","FUTIDX"}` for legacy (`canonical_data.py:595`) **[verified by me]**. BSE F&O absent entirely (`source_resolver.py:118` raises). |
| **Index data** | **Partial** | NSE index full history; BSE index floored at 2025-04-17 with no legacy source behind it (`bse_index_downloader.py:19`). Neither SME nor BSE index carries a source date column, so `DATE` is stamped from the request (`canonical_data.py:394`, `683`). |
| **Combined daily files** | **Partial** | Arrival-order independent and byte-stable, proven over all permutations (`combined_file_builder.py:367-475`; `tests/test_combined_file_builder.py:49-84`). But a missing dependency component suppresses the EQ file entirely and writes nothing (`date_join_coordinator.py:97-119` → `combined_file_builder.py:301-327`) **[verified by me]**. |
| **Symbol-wise history** | **Partial** | Deterministic, atomic, deduped, 11-column files (`symbol_history.py:270-289`); stable-ID registry merges renames across ISIN/security-code (`symbol_history.py:344-360`, `604-610`) — this genuinely works, so BSE histories do *not* split at era boundaries. Blocked at scale by unbounded RAM in `upsert_batch` (`symbol_history.py:683-684`, `748-782`) **[verified by me]**. |
| **Corporate-action adjustment** | **Partial** | Automatic fetch from both exchanges with cookie bootstrap (`corporate_actions.py:155-204`); genuine 2PC with before/after sha256 (`corporate_actions.py:523-561`). But the ratio parser mis-reads dates as ratios **[verified by me, executed]**, volume is never inverse-adjusted (`corporate_actions.py:495-500`), and only bonus/split/consolidation are handled (`corporate_actions.py:74-86`). |
| **Resume / crash-safety** | **Done** | SQLite WAL, `synchronous=FULL`, `BEGIN IMMEDIATE`, `busy_timeout=30000`, `PRAGMA integrity_check` on open (`pipeline_sqlite.py:52-117`, `189-233`); atomic multi-date `mark_many` (`pipeline_state.py:238-282`); per-symbol journal checkpoints resume a killed batch byte-identically (`tests/test_phase7_history_batch.py:130-186`). The strongest part of the codebase. |
| **Backfill at scale** | **Missing** | `max_concurrent_downloads: 1` + `rate_limit_delay: 0.5` serialise all four NSE segments onto one host lane (`config.yaml:35,39` → `transport_pool.py:39-40,96-118`). History finalization is O(entire run) in RAM. No batch cap: `HistoryBatchJournal.prepare()` has no `LIMIT` (`history_batch.py:187-213`). |
| **Query / retrieval layer** | **Missing** | `pipeline_state.sqlite3` holds two tables — `metadata` and `pipeline_dates` — and **no OHLCV anywhere** (`pipeline_sqlite.py:70-97`). Daily files are headerless (`base_downloader.py:444`), so column meaning lives only in `EQUITY_DAILY_COLUMNS` (`canonical_data.py:16-19`). Nothing enumerates `<EX>/SYMBOLS/` — grep returns only path construction. |
| **Packaging / release** | **Missing** | `PACKAGING.md`: no binary or bundle has ever been built. CI is Linux-only with a Nuitka *dry run*, no artifact, no macOS/Windows job. No `.icns`, no signing/notarization. No logging configuration anywhere in `src/`, `main.py` or `runtime_*.py`. |

---

## 3. WHAT IS GENUINELY GOOD

Not padding — these are real and worth protecting:

- **The era abstraction itself.** `source_resolver.py:29-139` returns `(url, era, kind)` and the era string is threaded into the parser (`bse_eq_downloader.py:127-130`) and recorded per date in the manifest (`base_downloader.py:670-678`). The parser never guesses. Compare the prototypes, where each script hardcoded one URL and one column list.
- **Fail-closed schema contracts.** `validate_source_schema` (`canonical_data.py:199-229`) rejects a wrong-era file *and* cross-checks the file's own date column against the requested date. Four independent date-verification layers exist for eras that carry a date (`canonical_data.py:214-229, 252-258, 278-283`; `combined_file_builder.py:219-227`; `data_manager.py:209`).
- **Transactional state.** SQLite/WAL with `synchronous=FULL` and `BEGIN IMMEDIATE`, a covering index whose use is asserted via `EXPLAIN QUERY PLAN` (`tests/test_phase7_pipeline_sqlite.py:102-113`), and a test that `os._exit`s mid-transaction and proves rollback (`:117-148`). This is better than most production code.
- **The history batch journal.** Per-symbol completion checkpoints in SQLite mean a killed batch resumes without redoing work and lands byte-identical to a clean run — and it is proven, not claimed (`tests/test_phase7_history_batch.py:130-186`). The verifier specifically tried to break the registry-ordering here and could not.
- **Corporate-action two-phase commit.** Stage file + `before_sha256`/`after_sha256` in the ledger, with `_recover_transactions` (`corporate_actions.py:344-418`) resolving both crash-before and crash-after cases by comparing the live file hash, invoked at the top of every upsert.
- **Consistent fail-closed corruption handling.** `VersionedJSONStore.read` quarantines damaged bytes and raises without touching the original (`state_store.py:102-124`, `48-73`); same for symbol histories, raw snapshots and the SQLite DB. `tests/test_state_integrity.py:73-120` verifies originals survive byte-for-byte.
- **Real TLS.** `transport_pool.py:65` and `http_client.py:44` both set `ssl=True`; no `verify=False` anywhere. Your own prototype `Getbhavcopy_BSE_Eq_New.py:30` used `verify=False` — you correctly did not carry that forward.
- **The gates actually pass.** 170 tests, ruff clean, mypy clean over 49 files, 73.8% coverage against a 70% gate, no test touches the network.

---

## 4. THE BIG STRUCTURAL ISSUES

### 4.1 A missing dependency component silently suppresses the entire EQ daily file — **blocker** **[verified by me]**

**What.** When any append option is on, `combined_required` is set (`main_window.py:281`) and `save_processed_data` sets `publication_deferred = True`, skipping the `.txt` write entirely (`base_downloader.py:435-447`). Publication then flows *only* through `DateJoinCoordinator.offer`, which returns `None` until every required component is present (`date_join_coordinator.py:77-79`). Unresolved dates fall to `finalize()` → `builder.record_failure`, which marks the stage failed and **writes no file** (`date_join_coordinator.py:97-119`; `combined_file_builder.py:301-327`). There is no EQ-only fallback.

I verified the shipped defaults make this fire immediately: `bse_index_append_to_eq: true` (`config.yaml`), `BSEIndexDownloader.FIRST_AVAILABLE_DATE = date(2025, 4, 17)` clamps only the INDEX list (`bse_index_downloader.py:19,25-31`), `dependencies_from_options` takes no date parameter at all (`combined_file_builder.py:269-288`), and `expand_selected_exchanges` auto-adds `BSE_INDEX` whenever `BSE_EQ` is ticked (`main_window.py:177-198`).

**Why it matters for your goal.** Select BSE_EQ and backfill 2015→2026: every BSE bhavcopy downloads, validates and persists a component, and `~/NSE_BSE_Data/BSE/EQ/2015-*-BSE-EQ.txt` is **never created** for ~10 years of dates. Those dates stay `complete=0` in `pipeline_dates` (`pipeline_sqlite.py:301-313`) and are re-downloaded on every future run forever (`base_downloader.py:584-592`). Same pattern for NSE whenever an old `sme*.csv` or `ind_close_all_*.csv` 404s — and `default_exchanges: [NSE_EQ]` already pulls in NSE_SME + NSE_INDEX.

**The fix.** In `DateJoinCoordinator.finalize()`, when a dependency is *permanently* unavailable, publish the EQ-only file from the persisted component via `CombinedFileBuilder.reconcile(exchange, date, ())` and mark the combined stage `degraded`/`disabled` rather than `failed`. Make `dependencies_from_options` date-aware so BSE INDEX is not required before 2025-04-17. Wire `reconcile()` into a post-run pass — today it exists only behind the manual `--rebuild-combined` CLI flag (`main.py:79-124`). **Immediate stopgap: untick `bse_index_append_to_eq` before any BSE backfill.**

### 4.2 BSE Equity 2023-01-01 → 2024-07-05 is an unmapped era — **blocker** **[verified by me]**

**What.** `source_resolver.py:98-103` maps the whole window 2022-08-17 … 2024-07-07 to one era, `bse-equity-bhavcopy-legacy`, whose schema requires `SCRIP ID, SCRIP_CODE, SC_GROUP, OPEN PRICE, …, TRADING_DATE` (`canonical_data.py:105-113`). Your own prototypes prove BSE flipped the schema *inside the identical filename*. I read both:

- `Bhavcopy_BSE_Eq_17_08_2022_to_31_12_2022.py:306` builds `BSE_EQ_BHAVCOPY_{ddmmyyyy}.ZIP` and parses `df['SCRIP ID']` (:142), `df['SC_GROUP']` (:145), `df['TRADING_DATE']` (:148).
- `Bhavcopy_BSE_Eq_01_01_2023_to_31_12_2023.py:317` builds the **same** `BSE_EQ_BHAVCOPY_{ddmmyyyy}.ZIP` but parses `df['TckrSymb']` (:153), `df['SctySrs']` (:156), `df['TradDt']` (:159), and drops UDiFF-only `Rsvd01..Rsvd04`, `FftyTwWkHgh` (:111-120).

The app cannot self-correct: `bse_eq_downloader.py:127-130` passes `era=source.era` explicitly, so the column-sniffing fallback at `canonical_data.py:421-427` never runs in production (only in tests, which call `normalize_bse_equity` with no era).

**Why it matters.** ~370 BSE trading days download successfully, then fail `validate_source_schema`, get quarantined, and re-enter `incomplete_dates` on every run — an infinite retry loop that also burns BSE rate limit. An 18-month hole in goal (a) with an error message that names the wrong cause.

**The fix.** Add `BSE_UDIFF_SCHEMA_IN_ZIP_START = date(2023, 1, 1)` and a fourth era `bse-equity-udiff-zip` that keeps the `.ZIP` URL but reuses the `bse-equity-udiff` schema and mapping (`canonical_data.py:114-121`, `429-442`). **Pin the flip date empirically first** — sample 2022-12-30 and 2023-01-02 — because 2023-01-01 is inferred from your prototype filenames, not from a sampled file. Add parametrised cases at those dates to `tests/test_canonical_data.py:87` and `tests/test_source_resolver.py:21`.

### 4.3 The storage model cannot hold the target dataset — **high**

**What.** Everything is CSV on both axes: daily files (`base_downloader.py:444`, `combined_file_builder.py:438`) and one text file per symbol (`symbol_history.py:270-289`). Any change to one symbol costs a full read + validate + sort + dedup + rewrite + a full backup copy. `upsert_batch` holds `history_cache` (every touched symbol's full history) and `incoming_cache` (one `pd.Series` per row) with no eviction, and writes only after everything is accumulated (`symbol_history.py:683-684`, `748-782`) **[verified by me]**. `HistoryBatchJournal.prepare()` selects all pending entries with no `LIMIT` (`history_batch.py:187-213`), and `main_window.py:416-421` finalizes once after gathering every segment — so the job **dies at the end, after hours of downloading**.

**Why it matters.** Your corpus is 3,318 NSE + 4,828 BSE symbol files today (`DOWNLOAD_PIPELINE_ARCHITECTURE_PLAN.md:107`) and 7,426 symbols/day for Select All (`PHASE_7_6_STAGED_OPTIMIZATION_REPORT.md:47`). A 2015-today backfill is ~8,100 date-entries × ~7,400 rows. This is the direct cause of the memory ceiling, the doubled write volume, the redundant hashing, and the fact that appending one trading day rewrites the entire symbol database plus a full second copy.

**The fix.** SQLite as system of record, text files as a regenerable export. Full proposal in §7.

### 4.4 Corporate actions can write wrong prices, and the guard has a hole — **high** **[verified by me, executed]**

**What.** `SPLIT_PATTERN = re.compile(r"(\d+\.?\d*)[\/\- a-z\.]+(\d+\.?\d*)", re.I)` (`corporate_actions.py:29`) is an unanchored `search`. I ran the real function:

```
'Sub-Division w.e.f. 12-08-2024 From Rs.10/- To Re.1/-'  -> ('split', 1.5)   # parsed 12 and 08 from the DATE; true factor is 10.0
'Bonus 1:1 and Face Value Split From Rs 10 To Rs 2'      -> ('bonus', 2.0)   # discards the 5:1 split entirely
'Consolidation From Rs 1 To Rs 10 w.e.f 05-06-2023'      -> ('consolidation', 0.1)  # correct — date trails
```

BSE descriptions concatenate `Purpose` + `Detail/Remarks` (`corporate_actions.py:112-119`), which is exactly where free-text dates live.

**Why it matters — but with an honest qualification.** The continuity guard is real and blocks most of the damage: `corporate_actions.py:502-512` compares the last adjusted pre-ex close to the first post-ex close and routes to `manual_review` with **no write** when the ratio leaves 0.67–1.50. The 10:1-parsed-as-1.5 case gives ratio ~0.15 → caught. What escapes silently is the narrow band — a mis-parse whose error factor lands inside 0.67–1.50 (a true 2.0 parsed as 1.5 gives 0.75) — plus any run where the ex-date bar is not yet downloaded, so `after` is empty and the guard is skipped. So the dominant failure mode is a **stalled `manual_review` entry with no GUI workflow to resolve it**, and the minority mode is silent corruption that is sticky across rebuilds.

Two adjacent defects compound it: volume, delivery quantity and `QTY_PER_TRADE` are never inverse-adjusted (`corporate_actions.py:495-500` loops only `("OPEN","HIGH","LOW","CLOSE")`), so after a 1:10 split your price series is continuous while volume steps 10× — poisoning every RVOL/turnover screen. (Note: `TOTAL_TRADES` must **not** be scaled — it is a transaction count, `canonical_data.py:439`.) And `CorporateAction.key` hashes the parsed *factor* (`corporate_actions.py:66-71`), so any future fix to `SPLIT_PATTERN` re-keys historical announcements and re-divides already-adjusted files.

**The fix.** Strip date-shaped substrings before matching; require `from X to Y` ordering and face-value tokens; return a *list* of actions per description; route >2 surviving numbers to `manual_review`. Drop `factor` from the key. Adjust VOLUME/DELIVERY_QTY/QTY_PER_TRADE. Stop tick-snapping (`corporate_actions.py:498-500`) — adjusted prices are synthetic and have no reason to sit on a 0.05 grid; snapping injects ±0.025 per bar and compounds across successive actions. Build a ~40-case unit table from real NSE `subject` / BSE `Purpose` strings.

### 4.5 The transport layer defeats its own retry machinery — **high**

Three separate defects that compound:

1. **Effective timeout is `total=5s` for the whole transfer** **[verified by me]**. `config.yaml:37` defines only `timeout_seconds: 5`; the three split keys exist in the dataclass (`config.py:34-36`) and are read (`config.py:175-177`) but are **absent from config.yaml**, so `_get_timeout_budget` (`async_downloader.py:243-258`) falls back to `ClientTimeout(total=5, connect=5, sock_read=5)`. `total` covers connect + all body reads. NSE FO UDiFF is a multi-MB ZIP. The GUI caps the spinbox at 30 (`main_window.py:1377-1378`). `tests/test_phase7_transport.py:24-26` passes only because its fixture supplies values production never has.
2. **Every connection failure is classified as a terminal SSL error.** aiohttp's `ClientConnectorError.__str__` is literally `"Cannot connect to host {host}:{port} ssl:{1} [{2}]"`, so a plain connection-refused string contains `"ssl"` and hits `async_downloader.py:338` — `should_retry: False` — before the network branch at `:347` is ever reached. The user is told "SSL certificate issue — server configuration problem" when their Wi-Fi blipped, and the retry layer never runs.
3. **The circuit breaker latches open.** `transport_pool.py:109-111` raises `NetworkError("Host circuit open for …", url=url)`, and `exceptions.py:75-81` appends the URL — so `_classify_error` substring-matches digits *inside the date in the URL* (`20240404` → "404 not published"). Worse, `async_downloader.py:451-452` records `success=False` when `slot()` itself raises, and `transport_pool.py:120-132` treats `status_code is None` as transient — so every date rejected during a cooldown re-arms the circuit. With `max_concurrent_downloads: 1` the circuit can never close for the rest of the run. One date timing out on all 3 attempts already reaches the threshold of 3, and the pool is shared run-wide (`main_window.py:376`) — **a single slow date can dead-end all four NSE segments**.

**The fix.** Add `connect_timeout_seconds: 10`, `read_timeout_seconds: 60`, `attempt_timeout_seconds: 300` to config.yaml. Classify by exception *type*, not substring. Give the breaker its own typed exception handled before any string matching, and move `record()` inside the slot context so only real request outcomes feed it.

### 4.6 Nothing verifies that what you have is complete or intact — **high**

**What.** There is no row-count gate anywhere: `validate_canonical_data` requires only `not frame.empty` (`canonical_data.py:271-272`), and `validate_daily_output` parses only the first line and the last non-empty line (`data_manager.py:186-199`). A 5-row placeholder bhavcopy is accepted as a complete trading day and never re-downloaded. sha256 digests are written into the manifest at `base_downloader.py:464`, `combined_file_builder.py:455` and **never read back** — the only checksum *verification* in the codebase is for raw snapshots and the update downloader. `get_missing_file_dates` computes expected dates only *between* the first and last existing filename (`data_manager.py:296-303`), so a truncated head or stale tail is invisible. Nothing enumerates `<EX>/SYMBOLS/` at all. And the only repair tooling is destructive `--rebuild-*` (`main.py:62-89`) — there is no read-only `--audit`.

**Why it matters.** For a database you intend to accumulate over years and trade off, "is it complete and self-consistent?" is currently unanswerable without mutating it.

**The fix.** A read-only `--audit` command: verify every manifest digest against disk, per-segment expected-row-count bands (trailing-20-session median, reject outside 50–150%), head/tail gap detection against `base_start_date` rather than the first filename, `SYMBOLS/*.txt` date coverage cross-checked against the checksummed `.state/raw` snapshots, and orphan detection. The raw snapshots already make this a pure derivation check.

---

## 5. RANKED ACTION LIST

Ordered by impact ÷ effort.

> ### ⭐ TOP 3 — do these before any historical backfill

| # | Action | Why | Effort | Files |
|---|---|---|---|---|
| **1** | **Publish EQ-only when a dependency is permanently unavailable**, and make `dependencies_from_options` date-aware. Stopgap today: untick `bse_index_append_to_eq`. | Unblocks ~10 years of BSE EQ daily files and stops an infinite re-download loop. Blocker. | M | `date_join_coordinator.py:97-119`, `combined_file_builder.py:269-288,301-327`, `base_downloader.py:435-447` |
| **2** | **Add the BSE `bse-equity-udiff-zip` era** for 2023-01-01 → 2024-07-07 (pin the flip date empirically at 2022-12-30 vs 2023-01-02 first). | Unblocks ~370 BSE trading days. Blocker. | M | `source_resolver.py:98-103`, `canonical_data.py:105-121`, `tests/test_source_resolver.py`, `tests/test_canonical_data.py` |
| **3** | **Add split timeout budgets to config.yaml** (`connect: 10`, `read: 60`, `attempt: 300`) **and classify transport errors by exception type**, not substring. | Two S-effort edits that restore the entire retry layer and make multi-MB FO downloads possible. | S | `config.yaml:34-44`, `async_downloader.py:243-258,317-413` |

| # | Action | Why | Effort | Files |
|---|---|---|---|---|
| 4 | Fix the circuit-breaker latch: typed `CircuitOpenError` handled before string matching; move `record()` inside the slot context; strip the URL from classifier input. | One slow date currently dead-ends all four NSE segments for a whole run. | S | `transport_pool.py:109-132`, `async_downloader.py:451-452`, `exceptions.py:75-81` |
| 5 | Cap the history batch: flush every N dates, evict `history_cache`/`incoming_cache` per bucket, replace the `iterrows` loop with `groupby("SYMBOL")`. | Turns an end-of-run OOM into a restart-safe streaming job. Without it the backfill cannot finish. | L | `symbol_history.py:683-782`, `history_batch.py:177-219,344-362` |
| 6 | Add a bundled static holiday table for past years; treat an empty API year as failure, not success. | Every pre-2025 exchange holiday currently becomes a permanent "failed" date. | M | `holiday_manager.py:84-99,220-282`, `data_manager.py:53-69` |
| 7 | Cap and expire pending-delivery retries; add an era branch (or `None`) for NSE delivery so absent sources mark the stage `disabled`. | Stops permanent re-download of dates whose delivery report will never exist, and lets segments report success. | M | `delivery_state.py:50-82`, `base_downloader.py:569-582,693`, `source_resolver.py:121-131` |
| 8 | Add a read-only `--audit` command (digest verification, row-count bands, head/tail gaps, SYMBOLS-vs-raw coverage). | The only way to answer "is my database trustworthy?" without mutating it. | M | new CLI in `main.py:62-89`, `data_manager.py:170-314`, `pipeline_state.py:118-122` |
| 9 | Add a row-count band per (exchange, segment) from the trailing-20-session median; reject/re-queue outliers. Make `validate_daily_output` count lines. | A partial exchange file currently passes every gate permanently. | S | `canonical_data.py:271-272`, `combined_file_builder.py:200-203`, `data_manager.py:186-199` |
| 10 | Fix the corporate-action parser (strip dates, require from→to, return a list) and drop `factor` from `CorporateAction.key`. | Prevents wrong divisors and prevents a future parser fix from re-dividing adjusted history. | M | `corporate_actions.py:29-38,66-71,74-86` |
| 11 | Inverse-adjust VOLUME, DELIVERY_QTY, QTY_PER_TRADE (**not** TOTAL_TRADES); stop tick-snapping adjusted prices. | Fixes every volume/RVOL/turnover screen across the whole history. Ship with a `.state` version marker + rebuild prompt. | S | `corporate_actions.py:495-500`, `symbol_history.py:427-434`, `tests/test_corporate_actions.py:66-67` |
| 12 | Validate ISIN format before it can act as a stable key; refuse to merge histories with overlapping dates; move to `.state/quarantine/merged/` instead of `unlink()`. | One malformed ISIN currently deletes a symbol's entire history irreversibly. | S | `symbol_history.py:322-343,344-360`, `canonical_data.py:315` |
| 13 | Add a single-instance lock (`fcntl.flock` on `<base>/.state/app.lock`) keyed on the resolved data root. | Two concurrent app copies silently destroy symbol histories and the registry. | M | `main.py:151-188`, `config.py:143` |
| 14 | Add `tests/conftest.py` with an autouse fixture setting `HOME`/`USERPROFILE` to `tmp_path`, plus a guard asserting the resolved base path is inside tmp. | `Path.home` patching does **not** stop `expanduser()`; the suite currently mkdirs your real data root. | S | new `tests/conftest.py`, `tests/test_gui_lifecycle.py:255,283` |
| 15 | Add a per-host `Referer` and a one-time cookie warm-up GET before the first archive request. Reconcile the 403 policy across the two code paths. | Preventive hardening against an NSE edge-policy change; the correct pattern already exists at `corporate_actions.py:167`. | M | `transport_pool.py:67-89`, `async_downloader.py:399-405` |
| 16 | Add `FIRST_AVAILABLE` per (exchange, segment) in the resolver; clamp every downloader; set the GUI picker minimum from the selected segments. | Today the picker offers 1990 and `price_source('NSE','SME',date(1995,1,1))` returns a URL. | S | `source_resolver.py`, `main_window.py:1265-1299`, all six downloaders |
| 17 | Add `'TS'` to `BSE_EQUITY_SERIES` and settle the Startup naming decision **before** release. | BSE Startup scrips are silently absent from every era; changing it later forces a migration of published files. | S | `canonical_data.py:35-37`, `PENDING_TASKS.md` |
| 18 | Raise `max_concurrent_downloads` to 2–3 with `rate_limit_delay: 0.3`; cache the NSE delivery payload per (URL, run). | Roughly halves the NSE acquisition critical path; the delivery file is currently fetched twice per date when EQ+SME are both selected. | S | `config.yaml:35,39`, `transport_pool.py:101`, `base_downloader.py:640-646` |
| 19 | Install a `RotatingFileHandler` in `run_gui_mode` + a Help → "Open Log Folder" action. | There is no logging config anywhere; a packaged binary produces zero diagnostics. | S | `main.py:151`, `main_window.py:1206-1217` |
| 20 | Drop the per-write symbol backup copy (or make it opt-in) and add retention for `.state/raw_revisions` and `.state/quarantine`. | Halves history-stage write I/O; the backup tree is written on every write and **read by nothing** (grep finds one hit, the writer). | S | `symbol_history.py:277-286,486-494`, `state_store.py:48-73` |

---

## 6. GAP LIST FOR THE TWO DATABASES

### Database (a) — Daily bhavcopy

**Must have**
- **BSE 2023-01-01 → 2024-07-05 era** (`source_resolver.py:98-103`). ~370 days, currently unfetchable.
- **EQ-only publication fallback** (`date_join_coordinator.py:97-119`). Without it, BSE EQ pre-2025-04-17 produces no file at all.
- **TURNOVER and PREV_CLOSE.** I grepped all of `src/` for `TOTTRDVAL|TtlTrfVal|NET_TURNOV|PREVCLOSE|PrvsClsgPric|TURNOVER|PREV_CLOSE` — **zero hits**. Your prototypes prove the source files carry them (`Bhavcopy_BSE_Eq_Befor_2022.py:99-102`, `Final_Bhavcopy_index_2024.py:81-83`). These are the only genuinely unrecoverable fields — everything else (SERIES, ISIN, SECURITY_ID, TOTAL_TRADES) is already persisted in `.state/raw` at full internal fidelity (`symbol_history.py:437-457`) and can be re-published offline.
- **A header row + schema version + per-segment manifest.** Files are headerless (`base_downloader.py:444`) and `validate_daily_output` accepts *either* 7 or 9 columns (`data_manager.py:201-203`), so two schema generations coexist as "valid" with no marker.
- **A per-segment earliest-available floor** so "how far back can I go?" is answerable from the UI.
- **Row-count / completeness gates** (§4.6) and a working historical holiday calendar.
- **Delivery match-rate telemetry** — the stage is marked complete on HTTP success, before the join (`base_downloader.py:678-691`).
- **`TS` group** added to `BSE_EQUITY_SERIES` **[verified by me: absent]**.

**Nice to have**
- Zero-price rejection and an OHLC sanity check (`LOW <= min(O,C) <= max(O,C) <= HIGH`). Currently only NaN and negative are rejected (`canonical_data.py:297-306`) **[verified by me]** — a `0,0,0,0,0` row publishes and reads downstream as a genuine −100% day.
- Stable BSE display symbol across eras. Pre-2022 rows are keyed by `SC_NAME` = `"ABB LTD."` (`canonical_data.py:459`) vs `"ABB"` later — with spaces and periods in a headerless CSV. Note this does *not* split the symbol-history database (the stable-key registry merges it correctly); it only makes the daily file's SYMBOL column unstable across the 2022-08-17 boundary.
- `encoding` fallback (cp1252) and `keep_default_na=False` in `read_report` (`canonical_data.py:176`); sorted ZIP member selection.
- NSE options (currently dropped at `canonical_data.py:575,595` **[verified by me]**), BSE F&O, BSE index pre-2025-04-17, NSE currency derivatives.
- Deterministic row order: `_finalize_equity` uses default quicksort (`canonical_data.py:323`) while the rest of the codebase carefully passes `kind="stable"`.

### Database (b) — Symbol-wise time series

**Must have**
- **A bounded, restartable history batch** (§4.3). Today it is the hard ceiling on any multi-year backfill.
- **Correct corporate-action factors** and **inverse volume adjustment** (§4.4).
- **ISIN format validation + non-destructive merge** (`symbol_history.py:322-343`) **[verified by me: `old_path.unlink()` is unconditional]**.
- **Ticker-reuse protection.** `_ensure_symbol_filename` (`symbol_history.py:186-204`) keys the filename on the ticker string alone and returns before any identity check; a delisted symbol reassigned to a new company appends into the old file, and `_deduplicate` (`symbol_history.py:217-231`) then destroys one row per shared date. Add an ISIN-keyed identity section with `first_seen`/`last_seen`.
- **An ISIN (or SECURITY_ID) column in `SYMBOL_HISTORY_COLUMNS`** (`canonical_data.py:25-28`) so a file is self-identifying and reuse/merge errors are detectable and reversible after the fact.
- **Gap detection and an audit for `SYMBOLS/`** — nothing in `src/` enumerates that tree.
- **A usable rebuild.** `rebuild_exchange` re-reads all snapshots per symbol (`rebuild_service.py:126,129,217-231`); at 3,318 NSE symbols it will not finish. It is also CLI-only — grep for `rebuild` in `src/gui/` returns nothing.

**Nice to have**
- Delisting/suspension status and listing metadata in the registry.
- Rights issues, dividends, demergers. `_action_type_and_factor` returns `(None, None)` for "Scheme of Arrangement / Demerger" **[verified by me]**, so a 20–50% demerger drop is stored as a real crash.
- A GUI workflow for `manual_review` actions — today they stall with only a status line.
- Prefer the *newer* row in `_deduplicate` rather than the higher-volume one **[verified by me: `sort_values(["DATE","_volume_rank"]).drop_duplicates("DATE", keep="last")`]**. Exchanges republish corrected bhavcopies and corrections frequently *reduce* volume, so the corrected row is currently discarded in favour of the erroneous one.
- Explicit `lineterminator="\n"` on `to_csv` (`symbol_history.py:276,317`) — output is currently `os.linesep`, so a macOS/Windows or cloud-synced data root produces byte-different files and breaks the sha256-based CA ledger.
- Per-symbol failure isolation — one bad row currently aborts the whole run's batch (`symbol_history.py:274` → `history_batch.py:389-401`).

---

## 7. RECOMMENDED TARGET ARCHITECTURE

**Proposal: SQLite becomes the system of record; the text files become a regenerable export view.**

```sql
-- one narrow table serves BOTH of your databases
CREATE TABLE eod (
  exchange    INTEGER NOT NULL,   -- or TEXT; small dim table
  segment     INTEGER NOT NULL,
  security_id INTEGER NOT NULL,   -- era-invariant: SC_CODE / SCRIP_CODE / FinInstrmId
  trade_date  INTEGER NOT NULL,   -- YYYYMMDD
  open REAL, high REAL, low REAL, close REAL, prev_close REAL,
  volume REAL, turnover REAL, total_trades REAL,
  delivery_qty REAL, delivery_pct REAL,
  series TEXT,
  PRIMARY KEY (exchange, segment, security_id, trade_date)
);
CREATE INDEX idx_eod_date ON eod (trade_date, exchange, segment);

CREATE TABLE securities (
  exchange TEXT, security_id INTEGER, isin TEXT,
  current_symbol TEXT, first_seen INTEGER, last_seen INTEGER,
  status TEXT,                    -- active | suspended | delisted
  PRIMARY KEY (exchange, security_id)
);
CREATE TABLE corporate_actions (...);  -- replaces corporate_actions.json
```

`WHERE trade_date = ?` **is** the daily bhavcopy. `WHERE security_id = ? ORDER BY trade_date` **is** the symbol time series. No second copy, no rename-merge gymnastics — a rename becomes an `UPDATE securities SET current_symbol = ?`, and ticker reuse becomes impossible because the key is the security, not the string.

**Rationale for SQLite specifically.**
- It is in the CPython stdlib, so Nuitka packaging is unaffected. `requirements.txt` deliberately carries only pandas/aiohttp/pyyaml/psutil/setproctitle/PySide6 — adding a native wheel is the single most common Nuitka packaging failure.
- You are already running it well: `pipeline_sqlite.py:52-117,189-233` has WAL, `synchronous=FULL`, `BEGIN IMMEDIATE`, `busy_timeout`, and `integrity_check`. This is a pattern the codebase has already proven.
- `executemany` under one transaction per date at `journal_mode=WAL, synchronous=NORMAL` loads ~7,400 rows in ~15–30 ms, so a 2,700-date store loads in 1–2 minutes instead of OOMing.
- Corporate actions become `UPDATE eod SET open=open/?, … WHERE security_id=? AND trade_date<?` — one statement instead of read + rewrite + rehash of a whole file. This alone removes §4.3, §4.4's stickiness, and most of §4.6.
- Size: ~40M rows × ~48 B ≈ 2 GB data + ~1 GB index.

**Honest trade-offs.**
- **Parquet/Arrow** is faster for full-universe scans but is immutable-file oriented: one split adjustment forces a row-group or partition rewrite, and daily appends mean rewriting a year file. Wrong shape for your write pattern.
- **DuckDB** would be genuinely better on the analytics side, but adds a 40–70 MB native dependency to every Nuitka target for queries a B-tree index serves fine. Crucially this is **not a one-way door**: DuckDB can `ATTACH` a SQLite file read-only, so ad-hoc cross-sectional analytics over 40M rows stays available later as an additive tool, not a format migration.
- **HDF5** adds a fragile C dependency and single-writer locking pain.
- **What SQLite costs you:** full-universe columnar scans ("all symbols above their 200-DMA") will be perhaps 3–10× slower than DuckDB/Parquet. For a single-user desktop screener at 8,000 symbols that is seconds, not minutes — acceptable. Concurrent multi-process writes remain single-writer, which is exactly this app's shape anyway (and you should add the instance lock from action #13 regardless).

**Migration path that does not break your downstream consumers** (`mark_screener`, `Mark Python`, `opentrader313`) — four independently shippable steps:

1. **Dual-write.** Write the DB alongside the existing files from the canonical frames already in memory. Zero consumer change, zero risk, fully revertible. Ship it and run it for a few weeks.
2. **Prove parity.** Add `export_daily(date)` / `export_symbol(symbol)` that regenerate text from the DB, then re-export and SHA-diff against the existing files. You already have exactly this harness pattern in `tools/phase7_*`.
3. **Flip publication to export-from-DB, dirty-set only.** This is where the 7,426-files-per-run cost dies, and where appending one day stops rewriting the whole store. Consumers still see identical `.txt` files at identical paths.
4. **Retire the redundant copies.** Once parity holds for one release, make `.state/raw` snapshots optional (the DB is now the rebuild source) and drop `.state/backups/history` — reclaiming roughly a third of the disk footprint.

Keep the era→schema binding in code. Only the **URL templates** should move to data: `config.yaml:79-111` currently declares `base_url`/`filename_pattern`/`date_format` for all six segments and **nothing reads them** (the only consumed field is `file_suffix` at `base_downloader.py:264`), while the YAML names `archives.nseindia.com` and the code uses `nsearchives.nseindia.com`. Make `source_resolver` read an era table from config with the current literals as built-in defaults, then delete or regenerate that stale block so it cannot mislead.

---

## 8. RISKS — ranked by likelihood × irreversibility

| # | Risk | Mechanism | Mitigation |
|---|---|---|---|
| **1** | **Two app instances silently destroy symbol histories, the registry and the CA ledger** | No lock file, no single-instance guard anywhere — grep for `flock/fcntl/QSharedMemory/QLocalServer/app.lock` returns **zero hits across `src/` and `main.py`** **[verified by me]**. All five mutexes are in-process `threading.Lock`. Both processes read `RELIANCE.txt`, each appends its own day, each `os.replace()`s the whole file. One day is permanently lost, with no error and no checksum to detect it. Also: two processes writing the same raw snapshot make one see a digest mismatch and quarantine a perfectly good file (`symbol_history.py:463-484`). | Action #13. Until then: never launch a second copy, and never start a run while another is finishing. |
| **2** | **A malformed ISIN merges two unrelated securities and deletes one file outright** | `_merge_renamed_file` calls `old_path.unlink()` unconditionally (`symbol_history.py:339`) **[verified by me]**, triggered purely by string inequality. `_stable_keys` accepts any non-empty ISIN; `canonical_data.py:315` applies no format check. `_deduplicate` then destroys one row per shared date, and the registry can flip back the next day and ping-pong. `.state/backups/history` does not save you — nothing reads it. | Action #12. Validate `^IN[EF][A-Z0-9]{9}$`; refuse merges with overlapping dates; quarantine instead of unlink. |
| **3** | **A wrong corporate-action factor silently rewrites years of OHLC, stickily** | Parser mis-reads dates as ratios **[verified by me, executed]**. The 0.67–1.50 continuity guard catches large errors but not a true-2.0-parsed-as-1.5. The ledger records it `applied`, so it is never retried, and `_apply_recorded_actions` replays the wrong factor onto any re-downloaded raw row — so rebuilds reproduce the corruption. | Action #10, #11. Also: do not run corporate actions on a historical backfill until the parser is fixed. |
| **4** | **Permanent silent data loss from unbounded retry loops** | Holiday-calendar failures, missing delivery reports, and both blocker eras all leave dates at `complete=0`, re-queued on every run forever (`pipeline_sqlite.py:301-313` → `base_downloader.py:584-592`). Every run reports "completed with errors", training you to ignore real errors, and each dead date re-downloads a full bhavcopy. | Actions #1, #2, #6, #7. |
| **5** | **A partial exchange file is accepted as a complete trading day, permanently** | Only `not frame.empty` is checked (`canonical_data.py:271-272`); `validate_daily_output` samples two lines (`data_manager.py:186-199`). The gap is invisible to `get_missing_file_dates` because the filename exists and both rows parse. | Action #9. |
| **6** | **Bit rot, partial cloud sync or an external edit is undetectable** | sha256 is recorded at write time (`base_downloader.py:464`) and **never re-checked** for daily files. Worse, for deferred EQ publication the `daily` record stores the *component's* digest and a path that does not exist yet (`base_downloader.py:454-464`) — so a future integrity checker would report false mismatches across the entire EQ series. Your data root is under `~/Documents`, which is exactly where iCloud sync lives. | Action #8. Also record the component digest under the existing `component_sha256` key rather than `sha256`. |
| **7** | **The test suite creates/touches `~/NSE_BSE_Data`** | `tests/test_gui_lifecycle.py:255,283` patch `Path.home`, but `config.py:143-146` uses `Path(base_folder).expanduser().resolve()` — and `expanduser()` reads `$HOME`, not `Path.home()`. There is **no `tests/conftest.py`** **[verified by me]**. Today only an idempotent `mkdir` reaches the real root because both tests reassign `base_data_path` on the very next line — **it is one line away from a test writing into your production database**. | Action #14. Highest-value/lowest-effort safety item in the list. |
| **8** | **A lost corporate-action stage file wedges all history processing permanently** | The ledger is fsynced (`state_store.py:134-144`) but the stage CSV is not (`corporate_actions.py:316-318`). A power loss between them leaves `status='prepared'` with an empty stage, and `_recover_transactions` then raises `StateStoreError` on every subsequent run — blocking corporate actions *and* every symbol-history batch, with no CLI to clear it. | Route `corporate_actions.py:318` through the fsyncing helper that already exists at `state_store.py:134-161`, and add `--discard-prepared-action <id>`. |
| **9** | **Downward bhavcopy corrections are silently rejected** | `_deduplicate` keeps the higher-VOLUME row **[verified by me]**. Exchange corrections frequently *reduce* volume (annulled trades), so the erroneous row is retained and the re-download appears to have had no effect. | Prefer the newer row; log when a re-download changes a stored value. |
| **10** | **Cross-platform line endings break the digest-based CA protocol** | `to_csv` with no `lineterminator` emits `os.linesep` — LF on macOS, CRLF on Windows (`symbol_history.py:276,317`). Any data root used from both, or synced, produces byte-different files and fails CA recovery with a hard `StateStoreError`. | Pass `lineterminator="\n"` explicitly; assert no `\r` in a test. |

**On `~/NSE_BSE_Data` specifically:** I did not read, write or enumerate anything under it during this review. The three code paths that could touch it unsafely are risks #1, #2 and #7 above. Risk #7 is the one to fix this week — it is a 15-line `conftest.py`.