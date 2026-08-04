# BSE SME/Startup group and symbol-suffix findings

Date: 2026-08-04 (Asia/Kolkata)
Status: Research complete; implementation pending
Repository branch at review: `main`

## Repository language convention

All new or modified repository documentation, source-code comments, and Git commit
subjects and bodies must be written in English. This convention applies to future
work; historical documents do not need a language-only rewrite unless they are being
substantively revised. Localized user-facing application text is outside this
engineering-record convention.

## Purpose

This document records the verified BSE group taxonomy, how the reference
`/Users/paresh/Desktop/Mark Python` project applies `_SME`, how the current
downloader handles those rows, and the pending implementation decisions. No user
data or application code was changed during this research.

## Verified facts

### 1. BSE uses two related classifications

| Group | Operational classification | Trading/settlement mode |
|---|---|---|
| `M` | Regular BSE SME | Rolling/net settlement |
| `MT` | Regular BSE SME | Trade-to-Trade/gross settlement |
| `MS` | BSE Startup sub-segment | Rolling/net settlement |
| `TS` | BSE Startup sub-segment | Trade-to-Trade/gross settlement |

BSE's 2025 Equity Segment Master Circular uses the broad label “SME securities”
for all four groups: `M`, `MT`, `MS`, and `TS`. The narrower operational
distinction remains useful: `M/MT` are regular SME groups, while `MS/TS` are the
Startup sub-segment under the BSE SME platform.

Official sources:

- [BSE Equity Segment Master Circular 2025](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=8a7fec3a-95bc-4bd2-82de-76e2a84e2915&id=20250429-51)
- [BSE notice confirming MT is Trade-to-Trade and M is rolling](https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page=20251003-56)

### 2. `SC_GROUP` is not the only current source field

- Legacy BSE bhavcopy schemas expose the group as `SC_GROUP`.
- Current UDiFF reports expose the same concept as `SctySrs`.
- The downloader must classify after mapping either source field to canonical
  `SERIES`; business logic must not depend only on `SC_GROUP`.

Official source:

- [BSE UDiFF field specification (`SctySrs`)](https://www.bseindia.com/downloads1/Annexure1_EOD_File_Formats_Equity_segment_Trading_side.pdf)

### 3. Settlement-cycle wording must be current

Rolling versus Trade-to-Trade describes net versus gross obligations. It does not
mean the old `T+2` calendar. BSE states that transactions in all equity-security
groups moved to `T+1` from 27 January 2023.

Official source:

- [BSE Secondary Market FAQ](https://www.bseindia.com/downloads1/FAQ_Secondary_Market.pdf)

### 4. Price bands are not safe classification keys

Historical Startup framework material describes 20%/5% bands for MS/TS, but BSE
surveillance measures and scrip-specific reviews can lower or otherwise change a
price band. Group/series is the classification authority; price band must not be
used to infer SME or Startup membership.

Official example:

- [BSE LT-ASM notice showing dynamic lower price bands and group transfers](https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page=20250818-53)

## Reference-project behavior

`Mark Python/bse_eod_downloader/stock_data.py` reads current `SctySrs` and applies
the suffix only when the value is `M` or `MT`:

```python
prefix = "_sme" if series in ("M", "MT") else ""
symbol_name = f"{symbol.upper()}{prefix.upper()}"
```

Despite the variable name `prefix`, the text is appended and therefore acts as a
suffix. The raw BSE bhavcopy is not rewritten; only the normalized DuckDB symbol
identity becomes, for example, `ABC_SME`. Its corporate-action path searches the
plain symbol and then the `_SME` form.

This behavior is an application naming convention, not an official BSE ticker
format.

## Current downloader behavior and gap

The current project already downloads regular SME and Startup rows through the
single BSE cash-market bhavcopy. A separate network request is not required.

`src/services/canonical_data.py` currently allows:

```python
{"A", "B", "M", "MS", "MT", "P", "R", "T", "W", "X", "XT", "Z", "ZP"}
```

Consequences:

- `M`, `MT`, and `MS` rows are included in `BSE_EQ`.
- None of them currently receive a suffix.
- `TS` is absent from the allowed set and can therefore be dropped.
- Legacy `SC_GROUP` and current `SctySrs` already map to canonical `SERIES`.
- BSE delivery joins use the security code, so a presentation suffix need not break
  delivery matching.
- Symbol-history and corporate-action code can resolve BSE identities through ISIN
  and security code, but migration and group-transition tests are required before
  changing existing names.

## Recommended model

Keep taxonomy separate from output naming:

```python
BSE_SME_GROUPS = {"M", "MT"}
BSE_STARTUP_GROUPS = {"MS", "TS"}
BSE_NON_MAINBOARD_GROUPS = BSE_SME_GROUPS | BSE_STARTUP_GROUPS
```

Recommended initial compatibility policy:

- Always include all four groups in the BSE cash download.
- Apply `_SME` automatically to `M/MT` only, matching the `Mark Python` contract.
- Do not add a user checkbox for identity-changing suffix behavior.
- Keep `MS/TS` explicitly classified as Startup. Decide their public naming contract
  before implementation: unchanged symbol, a documented `_STARTUP` suffix, or a
  deliberate broad `_SME` policy shared by every downstream consumer.
- Clarify the GUI label as `BSE Equity (includes SME and Startup)`.
- Add a separate logical BSE SME/Startup selector only if SME-only output is a real
  requirement. It must reuse one downloaded BSE cash payload rather than issue
  duplicate network requests.

This corrects the earlier over-broad suggestion that `_SME` must automatically be
applied to all four groups. BSE groups all four under its broad SME umbrella, but the
suffix is our application contract; exact `Mark Python` compatibility currently
means `M/MT` only.

## Pending decisions

1. Confirm the desired public symbol policy for Startup groups:
   - keep `MS/TS` unchanged;
   - use `_STARTUP`; or
   - expand `_SME` to all four groups and update downstream applications together.
2. Confirm whether users need a separate SME/Startup selection and output, or only
   correct classification inside the existing BSE Equity selection.
3. Confirm migration scope for existing unsuffixed BSE SME history files: future
   downloads only, or audited rebuild of historical derived outputs.

## Pending implementation tasks

### Classification and normalization

- Add `BSE_SME_GROUPS`, `BSE_STARTUP_GROUPS`, and their union.
- Add `TS` to accepted BSE cash groups.
- Apply the approved suffix policy in `normalize_bse_equity()` after source-era
  mapping and before canonical validation.
- Make suffixing idempotent so a symbol is never changed to `_SME_SME`.
- Ensure mutual-fund exclusion compares the base symbol or occurs before suffixing.

### Pipeline integration

- Preserve the existing single BSE price download and security-code delivery join.
- Ensure public daily files, component checkpoints, combined files, raw snapshots,
  symbol histories, and retry/rebuild paths receive the same normalized identity.
- Verify BSE corporate actions continue to resolve suffixed symbols by security code.
- Verify group migration in both directions (`M/MT` to main-board group and back)
  merges/renames history through stable ISIN/security ID without data loss.

### GUI and preferences

- Update the BSE label/help text to state that the cash file includes regular SME and
  Startup securities.
- Do not introduce a suffix checkbox unless a separate backward-compatibility
  requirement is approved.
- If a logical BSE SME/Startup selector is later added, partition the already
  downloaded normalized frame rather than creating another transport path.

### Tests and evidence

- Add a group matrix test for `M`, `MT`, `MS`, `TS`, and a main-board control group.
- Cover all three BSE source eras (`SC_GROUP` legacy variants and UDiFF `SctySrs`).
- Test delivery matching after suffixing.
- Test corporate-action lookup for suffixed symbols.
- Test stable-ID history migration, restart recovery, duplicate prevention, and
  suffix idempotence.
- Re-run 1/20/101-day single/Select-All parity benchmarks. Expected intentional SHA
  changes must be explained by symbol naming or newly included `TS` rows; row counts,
  schemas, and column order must remain controlled.
- Run the full suite in both `opentrader313` and `mark_screener` using isolated
  temporary data roots.

### Existing user-data safety

- Do not blindly rename files under `/Users/paresh/NSE_BSE_Data`.
- First produce a read-only migration inventory keyed by ISIN/security code and group.
- Back up affected registry/history files before any approved migration.
- Provide dry-run counts for unchanged, rename, merge, ambiguous, and missing-ID
  cases; stop on ambiguity rather than guessing.

## Separate deferred item

BSE FO remains a separate research/implementation scope. It is not part of this
cash-market SME/Startup suffix task.

## Deferred engineering evaluation: Polars

### Recorded decision

Do not migrate the production application from pandas to Polars now. Do not add
Polars or PyArrow to production requirements as part of the current work.

The Phase 7 profiling evidence shows that the dominant live Select-All cost is
symbol-history finalization and publication: approximately 7,426 individual history
files are written, with a measured median history batch of 10.936 seconds inside a
14.410-second staged median run. Polars can accelerate CPU-bound parsing, filtering,
sorting, joining, and concatenation, but it does not directly remove the current
network and per-file filesystem costs. A full migration would also touch fourteen
production modules and create avoidable output-compatibility risk around data types,
nulls, dates, stable ordering, duplicate resolution, and CSV serialization.

This decision should be revisited only through the isolated proof of concept below,
not through another general pandas-versus-Polars architecture discussion.

### Pending proof-of-concept scope

- Run the experiment in an isolated branch and temporary data root. Never modify
  `/Users/paresh/NSE_BSE_Data`.
- Install Polars only in an isolated test environment initially; do not change the
  production dependency files before the acceptance gates pass.
- Prototype only report parsing, canonical normalization, delivery joins, and the
  in-memory combined-frame preparation path.
- Keep symbol-history persistence, transactional state, corporate-action semantics,
  and final public CSV serialization on the current implementation during the first
  experiment.
- Do not combine this experiment with BSE SME/Startup naming, BSE FO, scheduling,
  state-storage, GUI, or packaging changes.
- Benchmark representative single-segment and Select-All runs for 1, 20, and 101
  trading days.
- Measure each target stage separately as well as end-to-end wall time, peak resident
  memory, event-loop lag, and conversion overhead at pandas/Polars boundaries.
- Verify exact public-output SHA, row count, column order, row order, null handling,
  date formatting, numeric formatting, duplicate resolution, and error behavior.
- Run the full automated suite in both `opentrader313` and `mark_screener` using
  isolated temporary data roots.
- Before any production adoption, validate application startup, dependency footprint,
  and actual Nuitka packaging on every supported target platform.

### Acceptance gates

Production migration may be proposed only if all correctness gates pass and the
proof of concept demonstrates at least one of these material benefits:

- at least 15-20% lower end-to-end Select-All wall time; or
- at least 35-40% lower time in the targeted parsing/normalization/join stages with no
  material boundary-conversion penalty.

The experiment should also target at least 25% lower peak memory for the 101-day
workload. A memory improvement alone is not sufficient when the existing workload is
already safely bounded.

If these gates are not met, close the experiment with an evidence report, remove the
experimental dependency, and retain pandas. If they are met, prepare a separate,
reviewable migration plan with an explicit rollback strategy; do not proceed directly
from the benchmark to a full rewrite.

### Higher-priority performance direction

Until profiling changes, prioritize reducing unnecessary symbol-history publication
work, safely skipping unchanged files, and evaluating bounded file-write improvements.
These changes address the measured bottleneck more directly than replacing the
DataFrame library.
