# NSE/BSE Data Downloader v1.1.0 (PySide6)

A desktop downloader that turns legacy and current NSE/BSE reports into one stable daily-file format.

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
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
- Optional legacy seven-column output and deterministic SME/Index combination.
- Calendar-based historical/custom date ranges with automatic mode retained.
- Individually collapsible Exchange, Date, Options, Progress and Status panels.
- Automatic version checks and update notifications.

Daily bhavcopy files remain official unadjusted market records. Corporate-action adjustments are applied only to symbol-wise histories.

## Installation

Requirements: Python 3.8 or newer and an internet connection.

```bash
git clone https://github.com/pparesh25/NSE_BSE_Downloader_PySide6.git
cd NSE_BSE_Downloader_PySide6
pip install -r requirements.txt
python main.py
```

Core dependencies include PySide6, aiohttp, pandas, NumPy and PyYAML.

## Using the app

1. Select the exchange segments.
2. Leave **Date Range** in automatic mode, or select a custom start/end date.
3. Choose delivery, FO open-interest, symbol-history and compatibility options.
4. Collapse panels you do not need, or use **View → Expand/Collapse All**.
5. Click **Start Download**.

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

The **Legacy 7-column output** option omits delivery/OI fields for older consumers. NSE delivery is matched by `SYMBOL + SERIES`; BSE delivery is matched by security code.

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

New v1.1 options:

```yaml
download_options:
  include_delivery_data: true
  include_fo_open_interest: true
  generate_symbol_files: true
  apply_corporate_actions: true
  legacy_seven_column_output: false
```

If a delivery report is late or temporarily unavailable, the price bhavcopy is still saved. The date is recorded under `.state` and retried on the next run.

Custom date mode intentionally allows existing historical dates to be downloaded again. Daily and symbol files are updated atomically rather than duplicated. The selected dates and each panel's expanded/collapsed state are remembered for the next launch.

## Testing

The release suite is validated in both Miniforge environments used for this
project:

```bash
/Users/paresh/miniforge3/envs/mark_screener/bin/python -m pytest -q
/Users/paresh/miniforge3/envs/opentrader313/bin/python -m pytest -q
```

The tests cover URL cutovers, legacy/current schemas, delivery keys, FO OI,
pending state, symbol reruns/renames, corporate-action idempotency and GUI date
range/collapse behavior. They also cover every NSE/BSE component arrival order,
restart rebuilds, disabled/failed dependencies, legacy 7-column combination and
deferred publication. The state-integrity matrix injects interrupted ledger
commits, failed history publication, corrupt JSON/CSV state, unexpected history
revisions and rebuilds from checksummed raw snapshots.

## Version history

### v1.1.0 (2026-07-31)

- Unified date-aware old/current download sources.
- Added NSE/BSE delivery fields and NSE FO OI fields.
- Added symbol-wise histories with audited corporate-action adjustment.
- Added pending delivery retry, atomic writes and legacy output compatibility.
- Added fail-closed state quarantine, crash-recoverable corporate-action
  transactions and raw-snapshot repair commands.
- Replaced arrival-order append logic with deterministic, restart-safe combined
  bhavcopy assembly and an explicit combined-file rebuild command.
- Added remembered calendar date ranges and collapsible GUI panels.
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
