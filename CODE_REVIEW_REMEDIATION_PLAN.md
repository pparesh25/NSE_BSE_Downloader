# NSE/BSE Downloader 1.1.0 — High-depth code review અને remediation plan

## Review baseline

- Review date: 2026-07-31 (Asia/Kolkata)
- Branch: `codex/unified-bhavcopy-symbol-history`
- Reviewed commit: `13c7416`
- Scope: `main.py`, `src/core`, બધા downloaders, `src/services`, `src/utils`,
  PySide6 GUI/update flow, configuration, build scripts, README અને tests.
- આ reviewમાં production code બદલ્યો નથી. આ દસ્તાવેજ implementation માટેનો
  ordered plan છે.

## Executive result

Current-day download pathના મૂળ કાર્યનો live પુરાવો સારો છે:

- NSE EQ, FO, SME, Index અને BSE EQ, Index — છ segmentના 2026-07-31 reports
  download અને normalize થયા.
- Daily output column order, delivery, FO OI, symbol histories અને બે દિવસના BSE
  corporate-action scenarioના data checks પાસ થયા.
- `mark_screener` અને `opentrader313` બંને envમાં હાલની `23` tests પાસ છે.

પરંતુ branchને release-ready ગણવી યોગ્ય નથી. Live Select All runમાં NSE combined
EQ fileમાંથી આખું SME segment ગાયબ રહ્યું. Static reviewમાં update/data transport પર
TLS verification બંધ, corporate-action idempotency transaction અધૂરી અને corrupt
history/stateને ખાલી માની overwrite કરવાની data-loss paths મળી. કુલ automated code
coverage માત્ર `32%` છે અને mypy `77` errors આપે છે; તેથી green tests આ critical
pathsને પ્રમાણિત કરતી નથી.

## Implementation status — `codex/release-hardening`

2026-07-31એ remediation શરૂ કરીને પ્રથમ પાંચ hardening phases પૂર્ણ કરવામાં આવી:

- P0-1 transport: market data, corporate actions અને shared HTTP clientમાં verified
  TLS ચાલુ; unverified connector/context દૂર.
- P1-2/P1-3: GUI-selected timeout runtimeમાં જળવાય છે; timeout/network/408/425/429/5xx
  માટે bounded retry, jitter અને useful final error અમલમાં છે.
- Update safety: માત્ર આ repositoryની immutable GitHub release/tag URL, SHA-256,
  download-size limit, `.part` publish, ZIP/path/link/bomb validation અને staged
  extraction સ્વીકારાય છે. Verified package metadata ન હોય તો GUI download બંધ રહે છે.
- Regression suite બંને requested environmentsમાં `51 passed`; verified-TLS live
  smokeમાં NSE EQ/FO/SME/Index, BSE EQ/Index તથા NSE/BSE corporate-action feeds પાસ.
- P0-2 exactly-once action state: prepared/committed transaction journal, staged
  history checksum અને restart recoveryથી history publish તથા ledger commit વચ્ચેના
  crash પછી factor ફરી લાગતું નથી.
- P0-3 fail-closed history/state: corrupt symbol CSV, registry, action ledger અને
  pending-delivery state quarantine થાય છે અને original bytes overwrite થતા નથી.
  Checksummed raw snapshots/revisions પરથી symbol, exchange, registry અથવા all rebuild
  માટે CLI repair commands ઉમેરાયા છે.
- Missing-history action હવે backfill પછી automatically reconcile થાય છે; filesystem
  slug collision માટે distinct stable symbol filenames બને છે.
- P1-4/P1-6/P1-7 strict data pipeline: URL-era schema registry, source-date equality,
  segment-aware numeric/key validation, error-payload quarantine, per-date resumable
  manifest અને strict filename/gap/integrity scan અમલમાં છે.
- `DateResult`/`SegmentResult` હવે partial date/segmentને success ગણતા નથી અને GUI
  stage-wise summary બતાવે છે. Latest 2026-07-31 live smokeમાં બધા છ segment pass:
  NSE EQ `2720`, FO `637`, SME `445`, Index `146`; BSE EQ `4261`, Index `76` rows.
- બંને requested Miniforge environmentsમાં regression suite `59 passed`; compileall
  અને changed-file Ruff gate પણ pass.
- P1-1 deterministic combined files: જૂનું arrival-order
  `MemoryAppendManager` દૂર કરીને persisted named-column components અને fixed
  `EQ → SME → INDEX` / `EQ → INDEX` reconciliation અમલમાં છે. Enabled dependency
  fail થાય તો જૂની public EQ file untouched રહે છે; restart/CLI rebuild સમાન SHA
  આપે છે અને legacy 7-column contract પણ જળવાય છે.
- Latest 2026-07-31 live all-segment reconciliation pass: NSE combined `3311`
  (`2720 + 445 + 146`) અને BSE combined `4337` (`4261 + 76`) rows; બંનેમાં
  component sum, blank Index delivery fields અને restart SHA verify થયા.
- Phase 4 પછી suite `75 passed`, changed-file Ruff/compile gates clean છે.
- Phase 5 calendar/settings/GUI lifecycle hardening: built-in → `config.yaml` →
  validated user preference precedence એક serviceમાં છે; market clock
  Asia/Kolkata-aware અને injectable છે; NSE official CM holiday API year-wise
  24-hour atomic cache તથા stale fallback સાથે વપરાય છે.
- GUI Stop/close હવે asyncio tasksને cooperatively cancel કરે છે; કોઈ `terminate()`
  path નથી. Segment/overall outcomes success, partial, pending, warning,
  repair-required, cancelled અને failed તરીકે typed signalsથી render થાય છે.
- Automatic update preference, skipped-version persistence/reset, manual forced check
  અને active update worker close guard પૂર્ણ છે. Offscreen real GUI startup pass અને
  official live calendar refreshમાં 2025–2026ના `38` entries verify થયા.
- બંને Miniforge environmentsમાં suite `85 passed`; changed-file Ruff, compileall,
  GUI smoke અને official-calendar live smoke pass છે.
- Phase 6 packaging-preparation શરૂ: source/Nuitka બંને માટે bundle-root config/QR
  resolution, compiled-module version detection, stable Qt/macOS identity અને
  `com.github.pparesh25.NSEBSEDownloader` bundle ID અમલમાં છે. ખોટા missing widget
  imports દૂર થયા છે.
- Build tooling હવે package install કે clean આપમેળે કરતું નથી; default dry-run
  resource/dependency/metadata validate કરીને quoted command જ બતાવે છે. Compilation
  માટે explicit `--build` અને cleanup માટે અલગ `--clean` જરૂરી છે. આ તબક્કે userની
  સૂચના મુજબ કોઈ Nuitka build ચલાવાયો નથી.
- બંને envમાં suite `93 passed`; macOS dry-run બંને Python 3.10/3.13 envમાં pass અને
  `dist/` artifact બન્યો નથી. Strict committed mypy config હવે `0` errors/39
  source files pass કરે છે. Current overall coverage હજુ `64%` છે, એટલે Phase 6
  complete નથી.

P0-1, P0-2, P0-3 અને Phase 3–5 માટે regression guards હવે હાજર છે. આગળનું
release-blocking કાર્ય Phase 6નું coverage/type/build/release-quality hardening છે.

## Severity અર્થ

- **P0 — release blocker:** security અથવા irreversible/double financial-data
  corruptionનો સીધો જોખમ.
- **P1 — high:** સામાન્ય workflowમાં ખોટો/અધૂરો output, retry failure, missing history
  અથવા unsafe GUI shutdownનો જોખમ.
- **P2 — medium:** configuration, observability, maintainability અથવા uncommon edge
  case જે production diagnosis/repairને મુશ્કેલ બનાવે.
- **P3 — low:** cleanup, dead code, type/documentation drift અને log noise.

## Findings — P0 release blockers

### P0-1: TLS verification સમગ્ર network/update pathમાં બંધ છે

Evidence:

- `src/utils/async_downloader.py:124-133` — market downloads માટે connector
  `ssl=False`.
- `src/utils/http_client.py:39-42` — version/update/holiday downloads માટે પણ
  `ssl=False`.
- `src/services/corporate_actions.py:155-159,188-192` — બંને action feeds માટે
  verification બંધ.
- `src/utils/update_checker.py:43-45,244-305` — mutable `main` branch ZIP download
  થાય છે; checksum/signature/tag binding નથી.

Impact: network attacker bhavcopy, corporate-action factor અથવા application update
બદલી શકે છે. Update ZIPને user code તરીકે manually replace કરવાની સૂચના હોવાથી આ
supply-chain issue છે.

Required fix:

- Default verified system CA context વાપરવો; host-specific bypass દૂર કરવો.
- જો કોઈ legacy BSE endpoint ખરેખર certificate failure આપે તો verified alternate
  official host અથવા narrowly pinned certificate policy વાપરવી; global bypass નહીં.
- Updateને immutable version tag/release asset સાથે bind કરવો અને published SHA-256
  અથવા signed manifest verify કર્યા પછી જ extract કરવું.
- Version metadata અને ZIP એક જ release identifierના છે તે verify કરવું.

Acceptance: invalid/self-signed certificate reject થાય; tampered ZIP/hash mismatch
પર update રોકાય; બધા six live downloads verified TLS સાથે પાસ થાય.

### P0-2: Corporate-action history write અને ledger commit atomic transaction નથી

Evidence:

- `src/services/corporate_actions.py:333` symbol history replace કરે છે.
- ledger માત્ર બધા groups પછી `src/services/corporate_actions.py:341` પર લખાય છે.
- `src/services/corporate_actions.py:214-223` corrupt ledgerને ખાલી ledger માને છે.

Impact: history rewrite પછી crash/kill/ledger-write failure થાય તો આગામી run એ જ
split/bonus ફરી લાગુ કરી priceને બીજી વાર divide કરશે. Corrupt ledger પણ એ જ
double-adjustment path ખોલે છે.

Required fix:

- Per-action write-ahead journal અથવા rebuild-from-raw transaction બનાવવી.
- Ledgerમાં `prepared` -> history checksum -> `committed` state machine રાખવી.
- Startup recovery incomplete transactionને rollback/rebuild કરે.
- Ledger parse/schema failureને quarantine અને user-visible hard error બનાવવો; empty
  state તરીકે ચાલુ ન રહેવું.

Acceptance: history write, ledger write અને process exitના દરેક failure-injection
point પછી rerunમાં OHLC exactly-once adjusted રહે.

### P0-3: Corrupt symbol history silently empty માની overwrite થાય છે

Evidence: `src/services/symbol_history.py:94-104` કોઈ પણ CSV parse failure પછી empty
DataFrame આપે છે; પછી `upsert()` નવી એક rowથી file atomically replace કરે છે.

Impact: એક damaged `reliance.txt` પર નવી date download કરવાથી સંપૂર્ણ જૂની history
ચેતવણી વગર ખોવાઈ શકે છે. `symbol_registry.json` માટે પણ `:50-57` પર સમાન silent
reset છે.

Required fix:

- Parse/schema/date failure પર write બંધ કરવું, original file quarantine/backup કરવી
  અને repair-required state બતાવવી.
- Raw snapshotsમાંથી deterministic `rebuild-symbols` service/command અમલમાં મૂકવો.
- Registryને raw snapshots પરથી rebuild કરી શકાય તેવો index બનાવવો.

Acceptance: corrupt history/registry fixture પર original bytes જળવાય, operation fail
loudly થાય અને repair command expected history પાછી બનાવે.

## Findings — P1 high priority

### P1-1: NSE/BSE append output task arrival order પર આધારિત છે — live confirmed

Evidence:

- `src/services/memory_append_manager.py:217-304` NSE EQ મળતાં ઉપલબ્ધ SME/Index જ
  append કરીને operation complete mark કરે છે; dependency પછી આવે તો ignore થાય.
- `:312-398` BSEમાં પણ no-data/partial stateને complete mark કરવાની સમાન logic છે.
- 2026-07-31 Select All live result: NSE EQ output `2866` rows = `2720 EQ + 146
  Index`; expected `3311` = EQ + `445 SME` + Index. `_SME` row એક પણ નહોતી.

Required fix:

- `MemoryAppendManager`ની event-order completion logic બદલે deterministic final
  reconciliation coordinator બનાવવો.
- Enabled componentsની required set date દીઠ પહેલેથી નક્કી કરવી; બધા selected tasks
  settle થયા પછી persisted segment daily files પરથી combined EQ ફરી build કરવી.
- Component status (`waiting/success/unavailable/disabled`) persist કરવો; restart પછી
  પણ reconciliation શક્ય હોવી જોઈએ.
- BSE textual sentinel (`"BSE SENSEX"`) આધારિત direct fallback દૂર કરવો.

Acceptance: NSE માટે EQ/SME/Indexની બધી 6 arrival permutations અને BSE માટે બન્ને
permutations byte-equivalent combined file આપે; restart test પણ સમાન output આપે.

### P1-2: GUI timeout setting લાગતી નથી — live confirmed

Evidence: `src/core/config.py:132-142` દરેક property access પર નવો dataclass આપે છે.
`src/gui/main_window.py:108,121,235` પર mutation તરત ખોવાઈ જાય છે. 30-second live GUI
request છતાં બધા HTTP logs `5s` હતા.

Required fix: validated runtime settings object Configમાં એક વાર materialize કરવો;
GUI override explicit setter/session parameterથી pass કરવો. Property mutation પર
નિર્ભર ન રહેવું.

Acceptance: worker 30 seconds પસંદ કરે ત્યારે constructed `ClientTimeout.total == 30`
અને log/result બંને 30 બતાવે.

### P1-3: Timeout `_attempt_download`માં swallow થવાથી retry ચાલતી નથી

Evidence: `src/utils/async_downloader.py:410-420` બહાર timeout retry logic છે, પરંતુ
`:554-563` અંદર `asyncio.TimeoutError` સહિત બધું failed resultમાં ફેરવે છે. Empty
`TimeoutError` stringના કારણે live log `Download attempt failed:` પર ખાલી હતો.

Required fix: timeout/network exceptionને typed result અથવા outer retry loop સુધી
propagate કરવી; HTTP `408/425/429/5xx` policy, `Retry-After`, bounded exponential
backoff+jitter અને final technical detail રાખવા.

Acceptance: simulated timeoutમાં configured 3 attempts થાય, retry counter 2 થાય અને
final messageમાં timeout duration/attempt count હોય.

### P1-4: Unknown/changed source schema અને invalid source date valid દેખાઈ શકે છે

Evidence:

- `src/services/canonical_data.py:74-78` missing column માટે all-null Series આપે છે.
- `:94-106` invalid/missing source date target filename dateથી silently ભરે છે.
- `:39-71` header-only/error-shaped CSVને explicit reject કરતું નથી.
- NSE Index `src/downloaders/nse_index_downloader.py:80-90` parsed source dateને
  unconditional target dateથી overwrite કરે છે.

Reproduction: invalid `TIMESTAMP` અને nonnumeric OHLC ધરાવતી row outputમાં
`DATE=20260731` તથા null CLOSE સાથે normalize થઈ.

Required fix:

- Era દીઠ required-column schema registry અને explicit schema discriminator.
- Source date parse success, requested-date equality, nonempty rows, symbol/key,
  numeric OHLCV અને unique-key validation save પહેલાં.
- Unexpected schema/date mismatchને quarantine કરવું; daily/symbol/state filesને
  touch ન કરવી.
- Source-data exceptions (જેમ NSE close-only indices) segment-specific documented
  validation profileમાં રાખવા.

Acceptance: renamed/missing column, wrong-date report, HTML/JSON error, header-only
file અને invalid OHLC fixtures hard-fail કરે; valid era fixtures pass કરે.

### P1-5: Append column alignment error છતાં unsafe original data પાછું આપે છે

Evidence: `src/services/memory_append_manager.py:451-474` category column પર
`fillna("")` TypeError થાય છે અને exception handler unaligned `append_data` return
કરે છે. Live logsમાં આ error NSE અને BSE બંને append વખતે આવ્યો.

Required fix: canonical framesને categoryમાં convert ન કરવી અથવા explicit dtype-safe
reindex કરવો; alignment failure hard failure હોવી જોઈએ. Deterministic coordinator
આ legacy path દૂર કરશે.

Acceptance: numeric/string/category inputsમાં exact target columns/order/dtypes મળે;
unmatched required column પર file rewrite ન થાય.

### P1-6: Daily file, raw snapshot, symbol files અને append state એક logical commit નથી

Evidence: `src/core/base_downloader.py:252-300` daily file પહેલાં publish થાય છે; પછી
symbol upsert/append failure થાય તો segment failed કહેવાય પણ આગામી automatic run
latest daily filename જોઈ date skip કરી શકે છે.

Required fix: per-date manifest/state machine (`downloaded`, `validated`, `daily`,
`symbols`, `delivery`, `actions`, `combined`, `complete`) અને resumable commit બનાવવો.
Daily public file valid રહી શકે, પણ date complete માત્ર બધા enabled stages પછી ગણવી.

Acceptance: દરેક stage પર injected failure પછી next run missing stage resume કરે અને
duplicate/double action ન થાય.

### P1-7: Automatic mode latest filename જ જુએ છે; વચ્ચેના gaps શોધતું નથી

Evidence: `src/core/data_manager.py:73-115,311-406` max dateને status માટે વાપરે છે.
Last date હાજર હોય તો જૂની missing/zero/corrupt date “up-to-date”માં છુપાય છે.
Regex પણ anchored નથી (`:42-48,97-105`).

Required fix: strict filename full-match + per-date manifest/integrity scan; expected
trading dates સામે missing/corrupt/incomplete dates reconcile કરવા. Pending delivery
અને symbol/action stages statusમાં સામેલ કરવા.

Acceptance: middle file delete/corrupt, `.txt.bak`, missing symbol stage અને pending
delivery fixtures “needs repair” બતાવે અને targeted repair કરે.

### P1-8: Holiday/calendar અને market-time logic stale તથા internally inconsistent છે

Evidence:

- `Market Holidays` અને `DateUtils.INDIAN_HOLIDAYS_2025` ફક્ત 2025 data ધરાવે છે.
- `src/utils/holiday_manager.py:87-89` hyphenated ISO/DD-MM datesને પહેલા hyphen પર
  કાપે છે; claimed formats parse થતા નથી.
- Cache expiry નથી; એક વાર જૂની nonempty cache load થાય પછી automatic refresh નથી.
- `src/core/data_manager.py:234-238` `holiday_skip: false` અવગણે છે.
- `src/utils/date_utils.py:271-314` local system timezone અને common hard-coded 18:00
  cutoff વાપરે છે; segment publication times અલગ હોઈ શકે.
- `src/core/data_manager.py:195-198` no-new-data rangeને `(start,start)` બનાવે છે,
  જેથી mixed selectionમાં already-current segment future/missing date ફરી અજમાવે.

Required fix: `Asia/Kolkata` aware clock injection, annually refreshed official
calendar/cache TTL, correct parser, config-respecting skip policy અને explicit empty
date range/result. Availabilityને per-source policy અથવા 404-as-pending semanticsથી
handle કરવી.

Acceptance: 2026 holiday, special weekend session, stale cache, holiday_skip=false,
pre/post publication time અને mixed-current selections માટે deterministic tests.

### P1-9: Persistent state corruption delivery retries ખોવાડી શકે છે

Evidence: `src/services/delivery_state.py:21-30` corrupt JSONને empty pending state
માને છે. Similar silent fallback symbol registry/action recordsમાં છે.

Required fix: shared versioned atomic state store, schema validation, previous-good
backup, quarantine અને recovery UI/report. Unknown state version પર fail closed.

Acceptance: truncated/invalid/version-newer JSON પર pending entries ખોવાય નહીં અને
userને repair action મળે.

### P1-10: Symbol correction semantics latest official rerunને હંમેશાં સ્વીકારતી નથી

Evidence: `src/services/symbol_history.py:79-92` same-date collisionમાં highest volume
row રાખે છે. Official corrected reportનું volume ઓછું હોય તો જૂની row જ રહેશે.

Required fix: provenance (`source_date`, report hash, ingested_at, revision`) રાખીને
validated newest rerunને authoritative કરવું; rename merge અને duplicate-source
priority અલગ rulesથી handle કરવા.

Acceptance: lower-volume correction સહિત same-date rerun daily અને symbol output
બન્નેમાં newest validated values આપે.

### P1-11: Corporate-action late backfill/no-prior-history ફરી apply થવાની guarantee નથી

Evidence: engine `no_prior_history` ledger status લખે છે (`corporate_actions.py:292-300`),
પણ action માત્ર current downloaded date range માટે ફરી fetch થાય છે
(`base_downloader.py:436-445`). User પછી ex-date પહેલાંનું history backfill કરે અને
fetch rangeમાં જૂનું ex-date ન આવે તો action unapplied રહે.

Additional risks:

- `normalize_bse_actions()`માં pandas NaN security code truthy હોવાથી stable ID
  `"NAN"` થઈ શકે (`corporate_actions.py:116-118`).
- Broad split regex unrelated numeric pair parse કરી શકે (`:21,25-31`).
- Continuity NaN explicit reject થતી નથી (`:313-323`).

Required fix: action catalog independently cache/sync કરવો; history change પછી affected
symbolના બધા known non-applied actions reconcile કરવા; identifiers/factors/schema
strict validate અને ambiguous descriptions manual reviewમાં મોકલવા.

Acceptance: ex-date પછી action discovery અને ત્યારબાદ old-date backfill test action
એક જ વાર apply કરે; NaN/ambiguous/continuity-NaN cases unchanged રહે.

### P1-12: Forced QThread termination file/state workflowને વચ્ચે કાપી શકે છે

Evidence:

- Stop flag માત્ર task/date પહેલાં check થાય છે (`main_window.py:189,227,243`);
  downloader loops તેને જોતા નથી.
- 5 seconds પછી `QThread.terminate()` (`:1084-1131`); close પર terminate + blocking
  `wait()` (`:1278-1297`).
- Update dialogનો worker ચાલતો હોય ત્યારે dialog close/remind/skip રોકાતું નથી.

Impact: GUI freeze, QThread destruction warning અથવા P0 corporate transaction gap પર
crash થઈ શકે.

Required fix: cooperative cancellation token/event દરેક date/retry/chunk/stage સુધી
propagate કરવો, asyncio tasks cancel/await અને sessions close કરવી. Window closeને
asynchronous shutdown flow બનાવવો; worker active હોય ત્યારે update dialog close guard.

Acceptance: download, retry, symbol upsert અને update download દરમિયાન cancel/close
tests 2 secondsમાં clean exit કરે; `terminate()`નો production call ન રહે.

### P1-13: User preferences atomic/validated નથી અને config defaultsને અજાણતાં હરાવે છે

Evidence:

- `src/utils/user_preferences.py:113-133` સીધું JSON overwrite; corruption પર defaults
  silently load થાય છે (`:87-111`).
- shallow default copies nested dict share કરી શકે (`:106,111,356-359`).
- User default append/suffix options `false`, જ્યારે `config.yaml:43-49` `true`; getters
  key હંમેશાં આપે એટલે config fallback ક્યારેય વપરાતો નથી.
- દરેક option readમાં નવો `UserPreferences` બને છે (`base_downloader.py:145-156`).

Required fix: એક injected settings service, deep copy, schema/type/range validation,
atomic temp+replace+backup અને documented precedence (`runtime > user > config`).

Acceptance: crash/concurrent save, malformed import, new-option migration અને clean
install default-precedence tests pass.

### P1-14: `Accept-Encoding: br` advertise થાય છે પરંતુ Brotli dependency નથી

Evidence: `async_downloader.py:113` અને `http_client.py:34` forced `br`; બંને tested
envમાંથી `opentrader313`માં Brotli module ઉપલબ્ધ નથી.

Required fix: aiohttpને supported encodings પસંદ કરવા દેવા અથવા Brotli explicit pinned
dependency/feature detection સાથે advertise કરવું.

Acceptance: Brotli વગર gzip/plain response અને Brotli સાથે encoded response બન્ને
સફળ; unsupported encoding request headerમાં ન જાય.

## Findings — P2 medium

1. **Stable filename collision:** `symbol_history.py:28-38` punctuationને `_`માં
   ફેરવે છે; બે distinct symbols એક fileમાં merge થઈ શકે. Stable ID-backed filename
   registry અને collision detection જરૂરી.
2. **No implemented rebuild workflow:** raw snapshots લખાય છે અને README “rebuildable”
   કહે છે, પરંતુ rebuild service/CLI/GUI નથી. P0/P1 recovery માટે તેને અમલમાં મૂકવો.
3. **Memory optimizer empty-frame crash:** `memory_optimizer.py:199-206` zero-row object
   columnમાં division by zero કરે છે; direct reproduction confirmed. Canonical data
   માટે category conversionનો લાભ ઓછો અને append defectનું કારણ છે.
4. **Partial range success:** shared equity અને individual FO/Index loops
   `success_count > 0` પર આખું segment success માને છે. Per-date structured result અને
   partial status જોઈએ.
5. **Notice error તરીકે જાય છે:** `base_downloader.py:136-143` delivery-pending/manual
   reviewને error signal આપે છે; GUI red error state અને console semantics ખોટી થાય છે.
   `info/warning/error` typed event બનાવવો.
6. **Integrity validator નામ પૂરતું છે:** `data_manager.py:462-500` માત્ર filename અને
   nonzero bytes તપાસે છે; columns, row count, requested date, duplicates/checksum નહીં.
7. **NSE/BSE output filtering hard-coded:** BSE mutual-fund symbol denylist
   `bse_eq_downloader.py:38-64` stale થઈ શકે અને legitimate security કાઢી શકે. Official
   instrument/series metadataથી rule કરવો અને removal report કરવી.
8. **Class-global append manager:** `base_downloader.py:95-98` પ્રથમ Config/data rootને
   process lifetime માટે bind કરે છે, memory/state cleanup થતું નથી. Multiple roots/tests
   wrong locationમાં લખી શકે.
9. **Automatic update preference ignored:** `UserPreferences.get_auto_check_updates()`
   છે, પણ `main_window.py:362-363` હંમેશાં check ચલાવે છે.
10. **Skip This Version no-op:** `update_dialog.py:433-436`; cache fallback method પણ
    normal check flowમાં વપરાતી નથી. SemVer prerelease parsing પણ supported નથી.
11. **Update download lifecycle:** partial ZIP સીધું final pathમાં લખાય છે; content
    length/maximum size/ZIP structure verify નથી. `.part` + atomic rename અને safe
    member validation ઉમેરવી.
12. **BSE binary log pollution:** `async_downloader.py:532-540` ZIP bytes decode કરીને
    production logમાં નાખે છે; prior live logમાં control/binary content આવ્યું. Preview
    માત્ર text content-type/debug modeમાં.
13. **Excess production debug logging:** ખાસ BSE Index/HTTP paths INFO સ્તરે URL,
    headers, previews અને sample rows લખે છે; useful structured summary સુધી ઘટાડવું.
14. **Config reload stale entries:** `config.py:252-256` reload પહેલાં
    `_exchange_configs` clear કરતું નથી; removed segment processમાં રહી શકે.
15. **Unused timers/state/dead code:** `main_window.py` status timer no-op method ચલાવે,
    update throttling variables partly unused; append managerમાં unused combined writer;
    `_should_retry_error()` dead છે.
16. **Build reproducibility:** dependency ranges unbounded છે, Python support READMEમાં
    3.8 કહેવાય છે જ્યારે built-in generic syntax માટે ઓછામાં ઓછું 3.9 જોઈએ; CI build/
    smoke matrix નથી. Supported Python/PySide/pandas versions pin/test કરવી.

## Findings — P3 quality/debt

- mypy હાલમાં `77` errors આપે છે; fallback DataFrame classes, implicit Optional,
  `any/callable` annotations અને abstract method signature mismatch મુખ્ય કારણો છે.
- `requirements-dev.txt`માં flake8 છે પરંતુ reviewed envમાં installed નથી અને CI quality
  gate નથી.
- Symbol second-day upserts `pd.concat` dtype FutureWarning હજારો વખત emit કરે છે;
  live confirmed. Batched per-symbol/grouped update અને explicit dtypes જોઈએ.
- `read_report()` ZIPમાંથી પ્રથમ CSV/TXT પસંદ કરે છે; expected member rule/checksum
  નહીં. Multi-member fixture ઉમેરવી.
- BSE Index availability date, common 18:00 message અને source cutovers code constants
  છે પરંતુ provenance/last-verified metadata નથી.
- Duplicate comments, unused `progress` locals, duplicate URL logs અને broad
  `except Exception` blocks diagnosisને મુશ્કેલ બનાવે છે.

## Test/diagnostic baseline

| Gate | Result |
|---|---:|
| `mark_screener` pytest | 23 passed |
| `opentrader313` pytest (offscreen GUI) | 23 passed |
| Python compileall | passed |
| Total `src` coverage | 32% |
| `DataManager` coverage | 11% |
| `MemoryAppendManager` coverage | 15% |
| `AsyncDownloadManager` coverage | 18% |
| `MainWindow` coverage | 15% |
| `HolidayManager` coverage | 0% |
| mypy | 77 errors |

Live evidence folder:
`/Users/paresh/Desktop/NSE_BSE_Live_Validation_2026-07-31`

Official-source data notes, defects નહીં:

- NSE Index reportમાં 20 close-only index rows અને 7 blank-volume rows હતા.
- NSE FOના 15 OHLC flagsમાંથી 13 zero/no-volume contracts અને 2 official sourceની
  low-volume inconsistencies હતી. Validatorમાં આને supported source conditions તરીકે
  encode કરવું, silently “repair” ન કરવું.

## Ordered implementation plan

### Phase 0 — Safety branch અને reproducible fixtures

1. હાલના branch પરથી dedicated remediation branch બનાવવી.
2. 2026-07-31 live raw payloadsમાંથી sanitized immutable test fixtures રાખવા; NSE/BSE
   દરેક legacy/current cutoverની existing samples ઉમેરવી.
3. Live output/state folderનો read-only backup અને hashes રાખવા.
4. Failure-injection helper અને fake HTTP transport બનાવવું.

Gate: current 23 tests unchanged pass; fixtures network વગર parse થાય.

### Phase 1 — P0 transport અને update hardening

Target files: `http_client.py`, `async_downloader.py`, `corporate_actions.py`,
`update_checker.py`, `update_dialog.py`.

1. Verified TLS default અને one shared HTTP policy.
2. Typed retry/error/result model; timeout swallow fix; supported Accept-Encoding.
3. Immutable release URL + version/checksum/signature manifest.
4. Update `.part` download, size/hash/ZIP member validation અને atomic publish.

Gate: TLS/tamper/timeout/429/5xx tests; six-segment verified-TLS smoke pass.

### Phase 2 — P0 state integrity અને recovery

Target files: `symbol_history.py`, `corporate_actions.py`, `delivery_state.py`, new
`state_store.py` અને `rebuild_service.py`.

1. Versioned validated state store with atomic backup/quarantine.
2. Corporate action write-ahead transaction and startup recovery.
3. Corrupt history fail-closed; raw-snapshot rebuild for one symbol/exchange/all.
4. Revision/provenance-aware symbol upsert and collision-safe stable filenames.
5. No-prior-history actionsને backfill પછી automatically reconcile કરવી.

Gate: corruption/crash matrixમાં zero silent loss અને exactly-once adjustment.

### Phase 3 — Strict canonical validation અને resumable per-date pipeline — complete

Target files: `canonical_data.py`, all downloaders, `base_downloader.py`,
`data_manager.py`.

1. Era schema registry + required/optional columns.
2. Source date/key/OHLCV/OI/delivery validation profiles.
3. Per-date manifest અને resumable stages.
4. Structured `DateResult`/`SegmentResult`; partial success GUI સુધી પહોંચાડવી.
5. Strict full-match filenames, gaps અને repair scanning.

Gate: schema mutation/property tests, middle-gap recovery અને stage failure resume.

### Phase 4 — Deterministic append replacement — complete

Target: `memory_append_manager.py`ને small deterministic `CombinedFileBuilder`થી replace
કરવો; final orchestration `DownloadWorker`/service layerમાં.

1. Enabled dependency set capture કરવી.
2. Segment tasks settle થયા પછી validated persisted daily files read કરવી.
3. Explicit named-column reindex, source-tag/duplicate policy અને atomic combined write.
4. Restart/rebuild commandથી combined EQ regenerate કરી શકાય.

Gate: arrival-order permutation, restart, one dependency failed/disabled અને legacy
7-column matrices byte-equivalent pass.

### Phase 5 — Calendar, configuration અને GUI lifecycle — complete

Target files: `config.py`, `user_preferences.py`, `holiday_manager.py`, `date_utils.py`,
`main_window.py`.

1. Single validated settings service અને clear precedence.
2. IST-aware injectable clock, official calendar TTL/refresh અને explicit empty range.
3. Cooperative cancellation/end-to-end worker shutdown; `terminate()` દૂર.
4. Typed GUI status: success, partial, pending, warning, repair-required, cancelled.
5. Auto-update/skip preferences અમલમાં મૂકવી અને active update worker close guard.

Gate: pytest-qt cancel/close/settings/calendar tests અને real GUI smoke.

Result: `85 passed` in both requested environments; real offscreen GUI startup,
cooperative close/cancel regression અને official NSE 2025–2026 calendar live refresh
pass. Qt offscreen platformે one-time fallback-font diagnostic આપ્યો; production
GUI failure નથી અને Phase 6 packaged-app smokeમાં native font/resource gate રહેશે.

### Phase 6 — Release quality, packaging અને documentation

Status: **in progress** — packaged-runtime code/dry-run preparation complete;
coverage, mypy, native build, signing/notarization અને fresh-user gates બાકી.

1. Core/services/downloadersમાં minimum 85% અને overall minimum 70% coverage gate;
   critical state/append/retry modules માટે branch coverage.
2. mypy `120 -> 0` complete; formatter/linter/type/test gates CIમાં ઉમેરવા.
3. `mark_screener` અને `opentrader313` બંને env; supported packaged Python version
   matrix સ્પષ્ટ કરવી.
4. macOS Nuitka build, fresh-user launch, QR/resource, update notification અને data
   folder permission smoke.
5. READMEની “atomic”, “rebuildable”, Python version અને output guarantees actual
   behavior સાથે reconcile કરવી.

Gate: clean checkoutથી build/install/run; no warnings; immutable release notes અને
checksum publish.

## Suggested commit sequence

1. `test: capture source fixtures and failure injection harness`
2. `fix: restore verified TLS and reliable retry semantics`
3. `fix: secure immutable update downloads`
4. `fix: make state and corporate actions crash recoverable`
5. `fix: validate schemas and resume per-date pipelines`
6. `refactor: make combined bhavcopy assembly deterministic`
7. `fix: unify calendar settings cancellation and GUI statuses`
8. `test: enforce coverage typing build and live smoke gates`

દરેક commit પછી બંને Miniforge env tests ચલાવવા; P0 state migration અને combined-file
refactorને એક જ મોટો commit ન બનાવવો જેથી rollback/bisect શક્ય રહે.

## Final release gate

Release/merge માત્ર ત્યારે:

- બધા P0 અને P1 findings બંધ અને regression testથી guarded હોય.
- Current dateના બધા 6 segment તેમજ દરેક URL cutoverની boundary fixtures pass થાય.
- Select Allમાં NSE EQ = EQ + enabled SME + enabled Index અને BSE EQ = EQ + enabled
  Index, arrival orderથી સ્વતંત્ર.
- Delivery late retry daily તથા symbol row update કરે અને pending state clear થાય.
- Corporate actions crash/retry/backfillમાં exactly once રહે.
- Verified TLS, immutable update artifact અને hash/signature validation active હોય.
- બંને requested env green, mypy green, coverage gates green અને macOS packaged GUI
  smoke pass હોય.
