# v1.1.0

> ### Upgrading from v1.0.1? Read **[UPGRADE.md](https://github.com/pparesh25/NSE_BSE_Downloader/blob/main/UPGRADE.md)** first.
> This release replaced PyQt6 with PySide6 and now needs Python 3.10 or newer. If you
> run from source, `pip install -r requirements.txt` is **required** — copying the new
> files over an old installation without it will stop the app from starting.

## Downloads

| Your system | File |
|---|---|
| Windows 10/11, 64-bit | `NSE_BSE_Downloader-1.1.0-windows-x64.zip` |
| Mac, Apple Silicon (M1–M4) | `NSE_BSE_Downloader-1.1.0-darwin-arm64.zip` |
| Linux, 64-bit | `NSE_BSE_Downloader-1.1.0-linux-x64.zip` |

Nothing else to install — Python and all libraries are bundled. Each file has a
matching `.sha256` if you want to verify your download:

```bash
shasum -a 256 -c NSE_BSE_Downloader-1.1.0-darwin-arm64.zip.sha256
```

**Intel Macs:** no prebuilt build. Run from source — see UPGRADE.md.

### First launch

These builds are not signed with a paid developer certificate, so both systems will
warn you once. This is expected.

- **macOS** — "cannot be opened because it is from an unidentified developer."
  **Right-click** (or Control-click) the app → **Open** → **Open**. Once only.
- **Windows** — "Windows protected your PC." **More info** → **Run anyway**.
- **Linux** — nothing unusual.

## What's new

**Unified date-aware downloads.** One downloader now handles every legacy and current
NSE/BSE report URL. No more separate scripts for old and new date ranges — NSE Equity
and F&O legacy archives and UDiFF, three eras of BSE Equity reports, and the NSE SME
filename change are all selected automatically by date.

**More data in every file.** NSE and BSE equity files now carry delivery quantity and
percentage; NSE futures carry open interest and change in OI. Equity, SME and futures
files keep a stable nine-column shape — if a field is unavailable it is left blank
rather than dropped.

**Symbol-wise history files.** Per-symbol text histories such as
`NSE/SYMBOLS/reliance.txt`, with split, consolidation and bonus adjustments applied to
pre-ex-date OHLC. Daily bhavcopy files stay unadjusted official records.

**Interrupted runs recover.** A resumable per-date manifest, atomic file replacement,
fail-closed state quarantine, and a corporate-action journal that cannot apply a factor
twice. Command-line repair commands rebuild histories from checksummed raw snapshots.

**Better date control.** Calendar-based custom ranges alongside automatic mode,
collapsible interface panels, and an IST-aware official NSE holiday calendar refreshed
daily with fallback to the last good copy.

**Stop actually stops.** Cancellation waits for the current atomic operation instead of
killing the worker, so published files stay valid.

Full changelog: see `VERSION_HISTORY` in
[version.py](https://github.com/pparesh25/NSE_BSE_Downloader/blob/main/version.py).

## Your existing data

**Nothing is deleted, moved or rewritten.** `~/NSE_BSE_Data/` is safe.

Three things to know:

1. **New files have more columns.** v1.0.1 wrote 7; v1.1.0 writes 9 for equity, SME and
   futures. Files you already have keep their old shape until you download that date
   again, so the folder will contain both. If your own scripts read these files, handle
   both widths or re-download the dates you care about.
2. **Symbol history files start empty.** They are built from downloads made *after* you
   upgrade. Re-download a date range to populate history for past dates.
3. **Your settings carry over.** A few v1.0.1 settings that no longer control anything
   are dropped. Worth a glance at the Settings panel once.

**Before your first big download:** four extra per-date steps are on by default, so
downloads are slower per date than v1.0.1. If you enable a segment you have never used
before, check the date range shown before clicking Start — it may be larger than you
expect.

## Going back

v1.0.1 still runs and still reads your data folder:
[1.0.1 release](https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/1.0.1).
Note it is not a clean round trip — 9-column files stay 9-column, and v1.0.1 ignores the
`.state` folder this version creates.

## Requirements

- Python 3.10 or newer (source installs only)
- macOS 13+, Windows 10+, or a current Linux
- An internet connection

## Problems?

Please open an issue with your operating system, `python3 --version`, and the exact
error text: https://github.com/pparesh25/NSE_BSE_Downloader/issues
