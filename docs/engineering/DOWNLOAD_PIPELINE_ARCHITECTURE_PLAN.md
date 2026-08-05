# NSE/BSE Downloader — Phase 7 download-pipeline architecture plan

## Scope અને હાલનો નિર્ણય

- Audit date: 2026-08-04 (Asia/Kolkata)
- Branch: `codex/release-hardening`
- Scope: GUI scheduler, NSE/BSEના છ segment, network retry/timeout, normalization,
  combined EQ publication, symbol histories, corporate actions અને pipeline state.
- આ તબક્કામાં production download code બદલવાનો નથી. પ્રથમ baseline telemetry,
  deterministic fixtures અને benchmark પછી feature-flag હેઠળ migration કરવી.
- Phase 6નું actual Nuitka build/signing/notarization હજી deferred છે.
- GitHub Actionsની હાલની Ubuntu `libEGL.so.1` dependency તથા Ruff-version/config
  mismatch પણ userની સૂચના મુજબ અલગ deferred maintenance item છે; આ optimization
  change સાથે mix કરવાનું નથી.

## Executive conclusion

મુખ્ય સમસ્યા server bandwidth નથી. હાલ છ segment async task તરીકે શરૂ થાય છે, પરંતુ
તે બધા એક જ worker event loopમાં synchronous pandas transform, CSV/state writes,
હજારો symbol-history rewrites અને corporate-action work પણ કરે છે. આ કામ ચાલે ત્યારે
બીજા in-flight HTTP reads schedule થઈ શકતા નથી; છતાં `aiohttp`નો 5-second **total**
timeout wall-clock પ્રમાણે ચાલતો રહે છે. તેથી single-segment run સ્થિર અને Select All
run slow/timeout થવું હાલના designથી સમજાય છે.

બીજો મહત્વનો નિષ્કર્ષ: current branchમાં NSE pure in-memory append કરતું નથી. NSE અને
BSE બંને `CombinedFileBuilder` વડે named-column component CSV પહેલાં disk પર મૂકે છે,
અને બધા segmentની આખી date range પૂરી થયા પછી તેને ફરી disk પરથી વાંચીને EQ file
બનાવે છે. આ deterministic/restart-safe છે, પરંતુ unnecessary disk round-trip અને
full-range head-of-line blocking આપે છે.

Recommended solution pure memory કે pure diskમાંથી એક નથી. **Bounded hybrid pipeline**
રાખવી: prepared frame memoryમાં date-level join માટે જીવંત રહે, durable checkpoint
એક વાર લખાય, અને memory pressure/restart વખતે checkpoint fallback બને. Network,
transform અને disk/history publicationને અલગ bounded stages તથા queuesમાં વહેંચવા.

## પુરાવા આધારિત current-state audit

### 1. Concurrency segment-level છે, date-level pipeline નથી

- `src/gui/main_window.py:287-323` દરેક selected segment માટે એક asyncio task બનાવે છે
  અને `asyncio.gather`થી બધા segmentની સંપૂર્ણ ranges settle થાય ત્યાં સુધી રાહ જુએ છે.
- `src/gui/main_window.py:405-484`માં combined reconciliation gather પછી જ ચાલે છે.
  એટલે 2026-04-01ના EQ/SME/Index તૈયાર થઈ જાય તો પણ તે તારીખનો final EQ તરત publish
  થતો નથી; સૌથી ધીમા selected segmentની આખી range પૂરી થવાની રાહ રહે છે.
- `src/core/base_downloader.py:512-676` અને `733-832`માં દરેક segment પોતાની dates
  sequentially કરે છે: download → normalize → save/history પછી જ next date.

### 2. Network manager global નથી અને દરેક dateએ session ફરી બને છે

- `src/core/base_downloader.py:564-570` તથા `770-774` દરેક date માટે નવો
  `AsyncDownloadManager`/`aiohttp.ClientSession` બનાવે અને તરત close કરે છે.
- તેથી DNS/TLS/keep-alive reuseનો મોટો લાભ ખોવાય છે.
- `src/utils/async_downloader.py:66-96`નો semaphore, statistics અને retry counters તે
  એક manager સુધી મર્યાદિત છે. છ segmentના managers વચ્ચે exchange/host-level global
  limit નથી.
- `config.yaml`નું `max_concurrent_downloads: 1` price/deliveryને એક manager અંદર
  sequential કરે છે, પણ Select Allમાં અલગ managers એકસાથે NSE/BSE hostsને hit કરે છે.
- દરેક manager 0.5-second delayથી લગભગ સાથે જ ઊઠે છે; shared rate clock/token bucket
  ન હોવાથી initial request burst બને છે.

### 3. 5-second timeoutમાં server time અને local event-loop starvation mix થાય છે

- `src/utils/async_downloader.py:107-143` અને `518-531` બંને જગ્યાએ
  `aiohttp.ClientTimeout(total=timeout_seconds)` છે; connect, first-byte અને body read
  માટે અલગ budgets નથી.
- pandas parsing, `to_csv`, JSON `fsync`, hashing, symbol file read/backup/rewrite અને
  corporate-action apply synchronous છે અને એ જ event-loop thread પરથી call થાય છે.
- તેથી timeout message server slow હોવાનો પુરાવો નથી; સ્થાનિક event-loop લાંબો સમય
  block થયો હોય તો પણ એ જ exception આવે છે.

### 4. Retry છે, પણ policy અને visibility અધૂરી છે

- Timeout, network, 408/425/429 અને 5xx માટે bounded three-attempt retry હાલ ચાલે છે.
- 2026-08-04ના actual `pipeline_manifest.json` snapshotમાં 358 date/segment records,
  16 explicit failed stages અને તે બધા `Server timeout after 5s (all 3 attempts
  failed)` હતા. એટલે “second attempt trigger નથી થતો” એવો GUI અનુભવ retry ન હોવાને
  બદલે retry telemetry GUI સુધી ન પહોંચતી હોવા તથા દરેક attempt સમાન contentionમાં
  પડતો હોવાને કારણે છે.
- HTTP 200 સાથે HTML error page `NetworkError` બને છે, પરંતુ તેનું message હાલના
  classifierના retry terms સાથે match થતું નથી; તે unknown/non-retryable બને છે.
- empty/truncated ZIP, CRC, unexpected MIME અને schema/source-date failure download
  success પછી validationમાં મળે છે; transient payload હોવા છતાં acquisition retry
  ફરી ચાલતો નથી.
- historical 404 અને latest “હજી publish નથી થયું” બંને એક non-retry outcome છે.
  Access-block/403 માટે session refresh, concurrency reduction કે circuit breaker નથી.
- Per-date manager close થતાં whole-run attempts, latency અને failure-rate statistics
  ખોવાય છે.

### 5. NSE અને BSE બંને combined output disk-based છે

- `src/core/base_downloader.py:363-454` EQ/SME/Index માટે component CSV save કરે છે.
  SME/Index માટે component ઉપરાંત public segment file પણ લખાય છે.
- `src/services/combined_file_builder.py:89-150` component atomically writes કરે છે;
  `305-405` તેને ફરી `pandas.read_csv`થી load, align અને concat કરીને final EQ લખે છે.
- Order deterministic છે: NSE `EQ → SME → INDEX`; BSE `EQ → INDEX`.
- તેથી BSE-only disk issue નથી. બંનેનું current checkpoint/reconcile mechanism સમાન
  છે. જોકે BSEના મોટા EQ row count અને વધારે symbol filesને કારણે તેની disk cost વધુ
  દેખાઈ શકે છે.

### 6. Symbol-history hot pathમાં severe write amplification છે

- `src/services/symbol_history.py:476-542` એક daily frameની દરેક row માટે existing
  symbol CSV read કરે છે, full history concat/deduplicate કરે છે, pre-update backup
  copy બનાવે છે અને આખી symbol file rewrite કરે છે.
- આ loop દરેક dateએ ફરી ચાલે છે. 100 dates download થાય તો એક જ symbol 100 વાર
  read/rewrite થઈ શકે છે, જ્યારે તેને runના અંતે એક batch mergeથી એક વાર લખી શકાય.
- Actual data snapshot: NSE `3,318` અને BSE `4,828` symbol files; `.state/backups`માં
  `8,250` files/`36 MB`. Raw snapshots `34 MB`, components `18 MB`, કુલ `.state`
  `91 MB` અને data root `144 MB` છે.
- Symbol history currently final daily EQ combine પહેલાં ચાલે છે, એટલે optional
  downstream output core network pipelineને block કરે છે.

### 7. JSON manifest state write amplification કરે છે

- `PipelineManifest.begin`, `mark`, `require_stage` અને `disable_stage` દરેક updateમાં
  આખું manifest read/validate/rewrite કરે છે.
- `VersionedJSONStore.write` formatted JSON write, `fsync`, previous-file copy અને
  directory `fsync` કરે છે. Durability સારી છે, પરંતુ stage/date count વધતાં repeated
  O(all-records) metadata I/O થાય છે.
- Actual manifest માત્ર 358 records પર આશરે `597 KB` છે અને દરેક stage transitionમાં
  ફરી લખાય છે.

### 8. Current reliability strengths જાળવવાની છે

- Source-era resolver અને canonical column validation.
- Raw payload hash, quarantine અને date/schema validation.
- Atomic component/public/history writes.
- Per-date resumability અને fail-closed state.
- Fixed component order, stable final column order અને restart rebuild.
- Pending delivery તથા corporate-action exactly-once/audit behavior.

Optimizationમાં આ guarantees નબળી કરવી સ્વીકાર્ય નથી.

## Target architecture

### A. RunCoordinator અને per-date DAG

એક top-level `RunCoordinator` selected optionsમાંથી idempotent work graph બનાવશે.
દરેક exchange/dateનો graph ઉદાહરણરૂપ:

```text
NSE EQ price ─┬─> validate/normalize EQ ──────┐
delivery ─────┘                                │
NSE SME ────────> validate/normalize SME ─────┼─> NSE date join ─> final NSE EQ
NSE Index ──────> validate/normalize Index ───┘

BSE EQ price ─┬─> validate/normalize EQ ──────┐
delivery ─────┘                                ├─> BSE date join ─> final BSE EQ
BSE Index ──────> validate/normalize Index ───┘

NSE FO ─────────> validate/normalize/OI ─────────> final NSE FO
```

Graph date-level રહેશે. કોઈ તારીખની required dependencies તૈયાર થાય ત્યારે તે
તારીખ publish થશે; unrelated date/segment માટે રાહ નહીં જુએ. Append option disabled
હોય તો તે dependency graphમાં જ નહીં આવે.

### B. ત્રણ અલગ bounded execution planes

1. **Network plane:** માત્ર async HTTP, response streaming/hash અને cheap envelope
   validation. તેમાં pandas અથવા disk writes નહીં.
2. **Prepare plane:** ZIP/XLS/CSV parse, row cleanup, schema mapping, delivery merge અને
   canonical DataFrame creation bounded worker executorમાં. આ event loop block નહીં કરે.
3. **Persistence plane:** checkpoint, final atomic files, state commits અને history
   batch writes માટે dedicated bounded writer. Slow disk network timersને અસર નહીં કરે.

દરેક plane વચ્ચે bounded `asyncio.Queue` રહેશે. Queue full થાય ત્યારે upstream
backpressure આવશે; unbounded dates/DataFrames memoryમાં નહીં ભરાય.

### C. Long-lived shared transport અને host-aware scheduling

- Run દીઠ NSE અને BSE માટે long-lived session/connector reuse કરવો.
- Semaphore manager/date દીઠ નહીં, normalized hostname દીઠ shared રાખવો.
- Initial conservative host concurrency benchmarkથી નક્કી કરવી; શરૂઆતમાં NSE માટે
  2 અને BSE માટે 1 reasonable test baseline છે, hard-coded guarantee નહીં.
- One shared monotonic request clock/token bucketથી calls evenly space કરવા.
- 429/403/timeout વધે ત્યારે AIMD-style concurrency decrease; stable success window
  પછી ધીમે increase. Configured hard maximum ક્યારેય cross ન થાય.
- Price/core dependencyને delivery, corporate-action અને repair/backfill કરતાં ઊંચી
  priority આપવી; optional request core bhavcopyને starve ન કરે.
- Maximum dates-in-flight configurable રાખવી, શરૂઆતમાં 2–4 benchmark window.

### D. Bounded hybrid memory + durable checkpoint

Prepared result typed object બનશે:

```text
PreparedSegment(
  run_id, exchange, segment, target_date,
  canonical_frame, source_url, source_era,
  payload_sha256, row_count, attempts, warnings
)
```

Policy:

- Normalize થયા પછી frame bounded in-memory cacheમાં date join માટે રહેશે.
- Stage acknowledge કરતાં પહેલાં durable component checkpoint એક વાર atomically લખાશે.
- Same-process happy pathમાં join સીધું prepared framesથી થશે; disk re-read નહીં.
- Restart, eviction અથવા memory limit વખતે checkpoint load fallback રહેશે.
- Final public EQ immutable assemblyથી એક વાર atomically publish થશે; existing EQમાં
  in-place append નહીં થાય.
- NSE અને BSE માટે એક જ coordinator/code path રહેશે; component order policy દ્વારા
  નક્કી થશે.
- Current named-column CSV પ્રથમ migrationમાં રાખી શકાય. Parquet/Arrow dependency
  ઉમેરતાં પહેલાં packaging/size benchmark જરૂરી છે; architecture તેના પર નિર્ભર નહીં.

આ design pure in-memory કરતાં crash-safe અને current pure disk reconciliation કરતાં
ઝડપી છે.

### E. Timeout, validation અને retry state machine

એક raw GUI “Response Timeout”ને total wall-clock તરીકે ન વાપરવું. Transportમાં અલગ
budgets રાખવા:

- connect/DNS budget,
- socket-read inactivity budget,
- bounded overall attempt budget.

પ્રારંભિક benchmark preset તરીકે connect 10s, socket read 30s, overall 60s લઈ શકાય;
final values live latency percentiles પરથી નક્કી કરવી. Local prepare/persist time HTTP
timeoutમાં count નહીં થાય.

Typed outcomes:

- `success`
- `retryable_transport`
- `retryable_payload` — HTML/empty/truncated/CRC/wrong MIME/transient schema envelope
- `deferred_not_published` — latest/current report હજી ઉપલબ્ધ નથી
- `terminal_not_available` — historical confirmed 404/non-trading report
- `access_blocked` — 401/403/anti-bot page
- `validation_failed` — repeated payload valid transport છતાં content contract invalid
- `cancelled`

Retry rules:

- timeout, DNS/reset, 408/425/429/5xx અને retryable payload માટે full-jitter backoff.
- `Retry-After` honor કરવું.
- 403/HTML anti-botમાં session/cookies refresh, host concurrency reduce અને એક
  controlled retry; repeated failure પર host circuit breaker, blind hammering નહીં.
- Latest 404ને delayed requeue કરી શકાય; historical 404 terminal/deferred policy source
  calendar અને publish windowથી નક્કી કરવી.
- URL-era alternate candidate માત્ર resolverમાં official candidate હોય ત્યારે જ.
- Attempts run-stateમાં persist થાય; exhausted task “skip” નહીં, explicit failed/deferred.

### F. Symbol histories અને corporate actions critical path બહાર

- Prepared EQ/SME rows run-scoped history buffer/journalમાં collect કરવી.
- Run/date windowના અંતે `exchange + symbol`થી group કરવું.
- દરેક affected symbol history એક વાર read, બધા incoming dates merge/deduplicate,
  એક pre-batch backup અને એક atomic rewrite કરવી.
- Delivery late-arrival પણ એ જ idempotent batch-upsert path વાપરે.
- Daily/combined output publish થયા પછી history batch શરૂ કરવો; core market files
  history workથી block ન થાય.
- Corporate-action fetch price downloads સાથે host contention ન કરે; history batch
  commit પછી exchange/date-range દીઠ એક વખત apply/reconcile થાય.
- GUI core files ready અને optional history/actions pending/complete અલગ બતાવે.

### G. Transactional state અને recovery

Stage-per-date whole-JSON rewriteને built-in SQLite/WAL અથવા append-only journal +
compactionથી બદલવું. Recommended default SQLite છે કારણ કે Python/Nuitkaમાં built-in,
transactional અને queryable છે.

State tablesનો minimum scope:

- runs/options/config snapshot,
- tasks/dependencies/priority,
- attempts/status/retry_at/error classification,
- stage checkpoints/hash/row count/output path,
- event timings અને final outcome.

Raw/component/public data SQLite blobમાં ન રાખવું. તે atomic files જ રહેશે. Migration
પહેલા existing JSON import થશે; successful verification સુધી original JSON untouched
રાખવું.

Crash recovery:

- `downloaded` પરંતુ `prepared` નહીં: payload checkpoint હોય તો re-prepare, નહીં તો
  download requeue.
- `prepared` પરંતુ `published` નહીં: component checkpointથી join/publish resume.
- final file hash match થાય તો idempotent success.
- history batch partially complete હોય તો symbol transaction journalથી remaining
  symbols resume.

### H. Observability અને GUI contract

દરેક attempt/stage માટે structured fields:

- queue wait,
- DNS/connect/TLS/TTFB/body timings,
- bytes/status/MIME/source URL,
- attempt number/retry reason/backoff,
- parse/normalize/delivery-merge duration,
- component/final/history/state write duration,
- event-loop lag, active host concurrency અને queue depth.

GUIમાં per segment/date `Downloading`, `Retry 2/3 in 2.1s`, `Preparing`, `Waiting for
NSE Index`, `Publishing`, `History queued`, `Completed`, `Deferred` અને `Failed` જેવી
state બતાવવી. “Skipped” શબ્દ માત્ર policy-confirmed non-work માટે રાખવો. User-facing
summaryમાં failed/deferred dates તથા Retry action રહે.

## Implementation phases

### Phase 7.0 — Baseline, telemetry અને behavior freeze

Code behavior બદલ્યા વગર timings/event-loop lag/attempt events ઉમેરવા.

- Current outputના SHA/row/column fixtures freeze કરવા.
- 1 day, 20 days અને 100+ days માટે single-segment અને Select All benchmark.
- Network bytes/time સામે prepare, state, component, symbol અને action time અલગ માપવું.
- Deterministic fault server/fixtures: timeout, slow chunks, reset, HTML 200, empty ZIP,
  CRC failure, 403, 404, 429 `Retry-After`, 500 અને cancel.
- Historical rollout feature flag: `pipeline_engine: legacy|staged`. Phase 7.6
  acceptance completed and the runtime flag was removed before the v1.1 merge.

Gate: metrics behavior/outputs ન બદલે; baseline report reproducible હોય.

### Phase 7.1 — Shared transport અને reliable retry

- `TransportPool`, `HostPolicy`, `DownloadAttempt` typed result.
- Long-lived sessions, global host limit/token bucket, split timeouts, circuit breaker.
- Acquisition validation retry અને persisted attempt telemetry.
- Corporate-action requests માટે separate low-priority lane.

Gate: injected failures correct number/classificationથી retry/defer થાય; host limit
cross ન થાય; no silent skip; single-date live six-segment smoke stable હોય.

### Phase 7.2 — Stage isolation અને bounded queues

- Network loopમાંથી pandas/disk/history calls દૂર.
- Prepare executor અને persistence writer ઉમેરવા.
- Bounded queue/backpressure/cancellation propagation.
- Memory budget તથા dates-in-flight enforcement.

Gate: heavy synthetic history write દરમિયાન in-flight slow HTTP false-timeout ન કરે;
event-loop lag target benchmarkમાં p95 100 msથી નીચે રહે.

### Phase 7.3 — Per-date join અને hybrid component cache

- `DateJoinCoordinator`, `PreparedSegment`, dependency graph.
- Same-process memory join, persisted checkpoint fallback.
- NSE/BSE one unified path and stable canonical-column matrices.
- Gather-after-all reconciliation was retained only through Phase 7.6 validation and
  removed before the v1.1 merge.

Gate: slow future dateથી earlier ready date block ન થાય; arrival-order permutation અને
restart byte-identical SHA આપે; dependency fail થાય તો existing public EQ untouched.

### Phase 7.4 — Transactional pipeline state

- SQLite/WAL schema, JSON importer, read-only verification અને rollback path.
- Batched stage transactions તથા efficient incomplete-date queries.
- Legacy JSON original backup/compatibility reader one transition release માટે.

Gate: kill/restart fault tests, corrupt DB recovery/backup અને manifest parity pass.

### Phase 7.5 — Batched symbol histories અને corporate actions

- Run-scoped symbol batch/journal; one read/write per affected symbol per batch.
- Delivery revision, rename, corporate-action transaction semantics preserve કરવા.
- Daily publication અને optional derived historiesના outcomes અલગ.

Gate: existing symbol/corporate-action fixtures byte-equivalent; 20/100-day benchmarkમાં
symbol I/O operations dates પ્રમાણે multiply ન થાય; crash recovery exactly-once રહે.

### Phase 7.6 — GUI, rollout અને soak

- Detailed attempt/stage UI, failed/deferred retry selection, queue/ETA summary.
- Historical legacy vs staged A/B benchmark; staged acceptance passed.
- Both Miniforge env tests, repeated live Select All soak અને calendar/source cutovers.
- Legacy engine removal was approved and completed before the v1.1 main merge.

Gate: સતત 20 one-day Select All live runsમાં zero silent skips; injected retry tests
100% deterministic; 100-day run memory configured capમાં; output parity 100%.

## Acceptance metrics

- Data correctness: current canonical row counts/order અને byte-stable filesમાં parity.
- Reliability: failure/deferred/terminal દરેક requested date માટે visible typed outcome;
  silently missing file શૂન્ય.
- Retry: attempt count, reason અને final result state/GUIમાં visible.
- Scheduling: per-host concurrency configured capથી ઉપર નહીં.
- Responsiveness: network event-loop lag p95 < 100 ms under benchmark load.
- Memory: prepared cache/queues configured budgetમાં; large rangeથી unbounded growth નહીં.
- Publication: earlier ready date slow unrelated date પહેલાં publish થાય.
- Recovery: forced terminationના દરેક stage પછી restart final hash સમાન આપે.
- Performance: 1/20/100-day baseline સામે wall time તથા disk operations report; staged
  engine default કરવા પહેલાં Select Allમાં material improvement ફરજિયાત.

## ફેરફાર ન કરવા જેવી shortcuts

- માત્ર timeout 5થી 30 કરવાથી architecture fix નહીં થાય; failure મોડું દેખાશે.
- માત્ર `max_concurrent_downloads` વધારવાથી NSE/BSE blocking/429 વધુ થઈ શકે.
- Pure in-memory append crash/restart guarantee તોડશે.
- Final EQ પહેલા લખીને પછી SME/Index append કરવું partial public data expose કરશે.
- Symbol filesની correctness છોડીને blind append performance માટે સ્વીકાર્ય નથી.
- Retry failureને success/skip ગણવું અથવા old public fileને current success માનવું નહીં.

## Recommended immediate next step

Phase 7.0ના instrumentation અને deterministic benchmark harnessથી શરૂ કરવું. તેની
baseline report approve થયા પછી Phase 7.1 shared transport/retry કરવો. Per-date hybrid
join Phase 7.3માં ત્યારે જ બદલવો જ્યારે transport અને stage isolation પહેલાં stable
થઈ ગયા હોય; નહિતર scheduling, retry અને publication changes એક સાથે હોવાથી regression
diagnose કરવું મુશ્કેલ બનશે.
