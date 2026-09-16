# v1.2.0

> ### Coming from v1.1.1? Nothing has to be migrated.
> Your data folder is read as it is. Two things are worth knowing before the first run.
>
> - **Daily files and symbol histories gain two columns: `TURNOVER` and `PREV_CLOSE`.**
>   Files you already have keep their old width until those dates are downloaded again,
>   and every data folder now carries a `SCHEMA.json` that says what each width means.
>   Neither value can be recovered for a date downloaded before this release, so
>   re-download a range if you want it filled.
> - **The date picker stops at the first day each NSE segment was ever published.** Older
>   dates were offered before, but no archive holds them, so they could never download.
>
> **Running from source:** nothing new to install; `requirements.txt` is unchanged since v1.1.1.

## Downloads

| Your system | File |
|---|---|
| Windows 10/11, 64-bit | `NSE_BSE_Downloader-1.2.0-windows-x64.zip` |
| Mac, Apple Silicon (M1–M4) | `NSE_BSE_Downloader-1.2.0-darwin-arm64.zip` |
| Linux, 64-bit | `NSE_BSE_Downloader-1.2.0-linux-x64.zip` |

Nothing else to install — Python and all libraries are bundled. Each file has a
matching `.sha256` if you want to verify your download:

```bash
shasum -a 256 -c NSE_BSE_Downloader-1.2.0-darwin-arm64.zip.sha256
```

**Intel Macs:** no prebuilt build. Run from source — see
[UPGRADE.md](https://github.com/pparesh25/NSE_BSE_Downloader/blob/v1.2.0/UPGRADE.md).

### First launch

These builds are not signed with a paid developer certificate, so both systems will
warn you once. This is expected.

- **macOS** — "cannot be opened because it is from an unidentified developer."
  **Right-click** (or Control-click) the app → **Open** → **Open**. Once only.
- **Windows** — "Windows protected your PC." **More info** → **Run anyway**.
- **Linux** — nothing unusual.

## What's new

### Check your data without changing it — `--audit`

```bash
python main.py --audit
python main.py --audit NSE_EQ BSE_EQ
```

It checks every recorded checksum against the file on disk, coverage from your configured
start date to the latest session the exchanges have published, row counts against the
neighbouring days, and every symbol history against the snapshots it was built from —
and it writes nothing, so it is safe to run at any time. Exit codes: `0` clean, `1`
findings, `2` the audit could not run.

### Turnover and previous close

Every era of every report the application reads now supplies both, stored in rupees.
Where an exchange publishes nothing the field is empty, never zero, so a BSE index shows
no turnover rather than a turnover of zero. The NSE index previous close is left empty on
purpose: deriving it from the published change is wrong for some indices.

| File | Columns |
|---|---|
| Equity, SME | `SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,DELIVERY_QTY,DELIVERY_PERCENT,TURNOVER,PREV_CLOSE` |
| Futures | `SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,OPEN_INTEREST,CHANGE_IN_OI,TURNOVER,PREV_CLOSE` |
| Index | `SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,TURNOVER,PREV_CLOSE` |
| Symbol history | `DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,SERIES,TOTAL_TRADES,QTY_PER_TRADE,DELIVERY_QTY,DELIVERY_PERCENT,ISIN,TURNOVER,PREV_CLOSE` |

The new columns come last, so a tool that reads the existing ones by position keeps
working. Daily files stay headerless; the `SCHEMA.json` beside them names the columns of
every width the folder can hold.

### Only dates the exchanges actually have

NSE equity from 1994-11-03, futures from 2000-06-12, indices from 2012-02-21 and SME from
2012-09-18. BSE equity has no fixed floor: its archive has gaps, and it does not reach
much before December 2016.

### Diagnostics you can send

**Help → Open Log Folder** opens `~/.nse_bse_downloader/logs/`. Logs never go inside your
data folder, and they are capped at 2 MB with five older copies kept. Every run starts by
recording the version, platform and certificate status, so attaching a log to an issue
identifies your build without anyone having to ask.

### The symbol-history stage shows its progress

After the last download finishes, symbol histories are brought up to date. That stage
used to show nothing at all — about twenty seconds of silence on a small folder, longer
on a large one — which looked like a frozen application. It now has its own row, with a
progress bar and a count.

### Groundwork for a database-backed archive — off unless you turn it on

This release contains the first steps of moving the archive into a SQLite database, each
proven byte for byte against real downloads. **All of them are off by default; with the
default settings nothing new is written.**

To try it: `dual_write_eod_database` mirrors every download into `.state/eod.sqlite3`
(about 450 MB a year for all six segments); `--verify-eod-parity` proves the database can
regenerate every file you have; `--republish-histories` brings symbol histories up to
date by extending them rather than rewriting them; and `--snapshot-revisions` shows what
an exchange changed when it republished a day.

## Fixed

- A delivery report that downloaded but matched nothing used to publish a day with every
  delivery field empty and no warning. The join is now measured, and `--audit` reports a
  day whose match rate falls far below its neighbours.
- The symbol-history stage no longer looks frozen after the downloads finish.
- The date picker no longer offers dates no exchange archive holds.

## Everything else

Unchanged from [v1.1.1](https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.1.1),
including the certificate store every build carries.

## Requirements

Python 3.10 or newer for source installs. The prebuilt applications need nothing
installed.
