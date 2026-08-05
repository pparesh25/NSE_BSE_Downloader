# NSE/BSE Data Downloader v1.1.0 (PySide6)

> ### Upgrading from v1.0.1? Read **[UPGRADE.md](UPGRADE.md)** first.
> v1.1.0 replaced PyQt6 with PySide6 and requires Python 3.10 or newer. Copying these
> files over an existing v1.0.1 install **without** running
> `pip install -r requirements.txt` will stop the app from starting. Prebuilt
> applications that need no setup are on the
> [releases page](https://github.com/pparesh25/NSE_BSE_Downloader/releases/latest).

A desktop downloader that turns legacy and current NSE/BSE reports into one stable daily-file format.

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![PySide6](https://img.shields.io/badge/GUI-PySide6-green.svg)
![License](https://img.shields.io/badge/license-GPL3.0-blue.svg)
![Version](https://img.shields.io/badge/version-1.1.0-brightgreen.svg)

## Features

- NSE Equity, Futures, SME and Index downloads.
- BSE Equity and Index downloads.
- Date-aware URL selection for old and current exchange report eras.
- NSE/BSE delivery quantity and percentage merged into cash-market files.
- NSE futures open interest and change in open interest.
- Symbol-wise text histories such as `NSE/SYMBOLS/reliance.txt`.
- Split, consolidation and equity-bonus adjustments on pre-ex-date symbol OHLC.
- Pending delivery retry, atomic file replacement and corporate-action audit state.
- Staged per-date publication with deterministic SME/Index combination.
- Calendar-based historical/custom date ranges with automatic mode retained.
- Individually collapsible Exchange, Date, Options, Progress and Status panels.
- IST-aware trading dates and a 24-hour cached official NSE holiday calendar.
- Cooperative Stop/close behavior with success, partial, pending, warning,
  repair-required, cancelled and failed outcomes.
- Responsive 720-pixel default window with complete date controls and Donate
  action kept beside the main controls.
- Automatic/manual version checks, remembered skipped versions and a reset action.

Daily bhavcopy files remain official unadjusted market records. Corporate-action adjustments are applied only to symbol-wise histories.

## Installation

Requirements: Python 3.10 or newer and an internet connection.

```bash
git clone https://github.com/pparesh25/NSE_BSE_Downloader.git
cd NSE_BSE_Downloader
pip install -r requirements.txt
python main.py
```

Core dependencies include PySide6, aiohttp, pandas, NumPy and PyYAML.
Source-mode launches also set the process title to **NSE BSE Data Downloader**;
packaged releases use the same native product and bundle identity.

### Upgrading from v1.0.1

Version 1.1 keeps the existing `~/NSE_BSE_Data/` data root and
`~/.nse_bse_downloader/` preference directory. Existing seven-column daily files
remain readable and are not rewritten merely by launching the application. A date
downloaded again is published under the current stable nine-column EQ/SME/FO
contract. Back up important user data before a major upgrade and use the official
GitHub Release assets rather than an archive from a mutable branch.

## Nuitka packaging and release trust

Nuitka packaging and project-owned cross-platform icons are configured. GitHub
Actions builds unsigned macOS ARM64, Windows x64, and Linux x64 test candidates;
a separate manual workflow is reserved for credential-gated macOS
signing/notarization and Windows Authenticode signing. No workflow publishes a
release or enables the updater automatically.

The default local commands only validate resources/dependencies and print the
build command:

```bash
python build_nuitka_cross_platform.py --target-platform=darwin
./build_nuitka_macos.sh
```

Neither command installs Nuitka, deletes output nor starts compilation without
an explicit `--build`. Runtime config/QR paths work independently of the launch
directory; user data and preferences remain outside the read-only app bundle.
See [PACKAGING.md](docs/engineering/PACKAGING.md) and
[RELEASE_TRUST_ASSETS.md](docs/engineering/RELEASE_TRUST_ASSETS.md) for build
evidence, credential setup, clean-machine verification, and publication gates.
Every internal engineering record lives under
[docs/engineering/](docs/engineering/).

## Using the app

1. Select the exchange segments.
2. Leave **Date Range** in automatic mode, or select a custom start/end date.
3. Choose delivery, FO open-interest and symbol-history options.
4. Collapse panels you do not need, or use **View → Expand/Collapse All**.
5. Click **Start Download**.

**Stop Download** requests cooperative cancellation. The app waits for the
current atomic operation instead of forcibly terminating its worker thread, so
already-published files remain valid. Closing the window while download/update
work is active follows the same safe shutdown path.

Files are stored under `~/NSE_BSE_Data/` by default.

## Output contracts

NSE/BSE Equity and SME:

```text
SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,DELIVERY_QTY,DELIVERY_PERCENT
```

NSE Futures:

```text
SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,OPEN_INTEREST,CHANGE_IN_OI
```

Index files keep seven columns:

```text
SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME
```

Equity, SME and FO files always retain their stable nine-column shape. If delivery
or FO open interest is disabled or unavailable, the corresponding optional fields
are blank rather than removed. NSE delivery is matched by `SYMBOL + SERIES`; BSE
delivery is matched by security code.

When the append options are enabled, the app always assembles combined cash
files in a fixed order, independent of concurrent download completion order:

- NSE: `EQ → SME → INDEX`
- BSE: `EQ → INDEX`

Each segment is first saved as a validated named-column component. The public EQ
file is replaced atomically only after all enabled dependencies succeed. A
failed or interrupted dependency leaves the previous public EQ file untouched
and the manifest schedules that date for repair on the next run. Index rows are
aligned by column name, so the two delivery fields remain blank.

## Data organization

```text
~/NSE_BSE_Data/
├── .state/                 # Internal pending/action/raw rebuild state
│   └── components/         # Validated inputs for combined-file restart/rebuild
├── NSE/
│   ├── EQ/
│   ├── FO/
│   ├── SME/
│   ├── INDEX/
│   └── SYMBOLS/
│       └── reliance.txt
└── BSE/
    ├── EQ/
    ├── INDEX/
    └── SYMBOLS/
```

Each symbol file has one header and one row per date:

```text
DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,SERIES,TOTAL_TRADES,QTY_PER_TRADE,DELIVERY_QTY,DELIVERY_PERCENT
```

Symbol files are sorted, idempotently updated and renamed/merged using ISIN or exchange security code when available. Split, consolidation and equity-bonus actions adjust only pre-ex-date OHLC. Volume, delivery fields and FO OI are not adjusted.

State files and symbol histories are validated before any update. Damaged bytes
are preserved under `.state/quarantine` and the operation stops with a
repair-required error instead of treating corrupt state as empty. Corporate
actions use a prepared/committed journal, so an interruption between history
replacement and ledger commit is recovered without applying the factor twice.

Raw snapshots include checksum metadata; replaced revisions are retained under
`.state/raw_revisions`. They can be used for explicit repairs:

```bash
python main.py --rebuild-symbol NSE RELIANCE
python main.py --rebuild-exchange BSE
python main.py --rebuild-registry
python main.py --rebuild-all
python main.py --rebuild-combined NSE 2026-07-31
```

Symbol rebuild commands validate snapshot checksums and replay committed
corporate actions before publishing repaired histories. `--rebuild-combined`
uses the current append preferences and persisted EQ/SME/Index components.
Existing files remain in place when validation or transaction recovery cannot
be completed safely.

## Date-aware report support

The application automatically selects the proper official source:

- NSE Equity/FO: legacy archives before 8 July 2024; UDiFF reports from that date.
- BSE Equity: ISIN legacy reports before 17 August 2022, second-generation reports through 7 July 2024, and UDiFF reports from 8 July 2024.
- NSE SME: two-digit-year filenames through 10 October 2025 and four-digit-year filenames from 13 October 2025.

Users do not need separate old/new downloader scripts.

## Configuration

Application defaults are in `config.yaml`. Per-user choices are saved to:

```text
~/.nse_bse_downloader/user_preferences.json
```

Effective settings use one precedence rule: built-in schema defaults, then
`config.yaml`, then validated saved user choices. Unknown keys and invalid
types are discarded or bounded before use. Automatic update checks can be
disabled from **Settings**; a skipped version can be reset there, while
**Help → Check for Updates** always performs a manual check.

Market-date decisions use Asia/Kolkata time. Trading holidays are read from
NSE's official capital-market calendar by year, cached for 24 hours, and
refreshed automatically. If refresh fails, the last valid cache is retained.

New v1.1 options:

```yaml
download_options:
  include_delivery_data: true
  include_fo_open_interest: true
  generate_symbol_files: true
  apply_corporate_actions: true
```

If a delivery report is late or temporarily unavailable, the price bhavcopy is still saved. The date is recorded under `.state` and retried on the next run.

Custom date mode intentionally allows existing historical dates to be downloaded again. Daily and symbol files are updated atomically rather than duplicated. The selected dates and each panel's expanded/collapsed state are remembered for the next launch.

## Testing

Run the suite on both supported Python versions:

```bash
python -m pytest -q
```

The maintainer validates each release on Python 3.10 and 3.13 before publishing;
GitHub Actions runs the same suite on both.

The tests cover URL cutovers, legacy/current schemas, delivery keys, FO OI,
pending state, symbol reruns/renames, corporate-action idempotency and GUI date
range/collapse behavior. They also cover every NSE/BSE component arrival order,
restart rebuilds, disabled/failed dependencies, old component in-memory upgrade and
staged publication. The state-integrity matrix injects interrupted ledger
commits, failed history publication, corrupt JSON/CSV state, unexpected history
revisions and rebuilds from checksummed raw snapshots. Lifecycle coverage also
checks cooperative cancellation/window close, settings precedence, skipped
updates, IST boundaries, official-calendar parsing, TTL refresh and stale fallback.

GitHub Actions runs the suite on Python 3.10 and 3.13 and fails below 70%
coverage. Ruff, mypy and a no-build Nuitka command validation are separate
release gates. The equivalent local commands are:

```bash
python -m ruff check .
python -m mypy src main.py app_metadata.py runtime_paths.py runtime_identity.py build_nuitka_cross_platform.py
python -m pytest --cov=src --cov=main --cov=runtime_paths --cov=runtime_identity --cov=app_metadata --cov=version --cov-fail-under=70
python build_nuitka_cross_platform.py --target linux
```
They also verify bundle-root config/QR lookup, compiled-module version detection,
platform-specific Nuitka command generation and the default no-build guard. The
strict project mypy configuration currently passes all 45 source files.

## Version history

### v1.1.0 (2026-07-31)

- Unified date-aware old/current download sources.
- Added NSE/BSE delivery fields and NSE FO OI fields.
- Added symbol-wise histories with audited corporate-action adjustment.
- Added pending delivery retry, atomic writes and stable nine-column EQ/SME/FO
  output contracts.
- Added fail-closed state quarantine, crash-recoverable corporate-action
  transactions and raw-snapshot repair commands.
- Replaced arrival-order append logic with deterministic, restart-safe combined
  bhavcopy assembly and an explicit combined-file rebuild command.
- Added remembered calendar date ranges and collapsible GUI panels.
- Added IST-aware official holiday refresh, validated settings precedence,
  cooperative cancellation and typed GUI completion outcomes.
- Added remembered update-check and skipped-version controls.
- Prepared deterministic Nuitka app metadata, bundle-root resources and an
  explicit dry-run/build split; no packaged artifact is included in this release.
- Fixed the NSE SME filename-era change and HTML-as-data responses.

### v1.0.1 (2025-08-07)

- Improved download logging and market-hours behavior.
- Migrated the GUI runtime to PySide6.
- Added cross-platform Nuitka build tooling.

### v1.0.0 (2025-07-31)

- Initial multi-exchange production release.

## License and support

Licensed under GPL-3.0; see [LICENSE](LICENSE). Please use GitHub Issues for bugs and support.

© 2026 Paresh Patel. All rights reserved.
