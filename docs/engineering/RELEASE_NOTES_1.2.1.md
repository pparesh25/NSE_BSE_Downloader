# v1.2.1

> ### Coming from v1.2.0? Two things changed.
> - **The database is now collected from your first download.** Every download is also
>   written into `.state/eod.sqlite3` beside your data — about 450 MB a year for all six
>   segments. Nothing reads it yet; it is there so that the features which will read it
>   have something to read. `dual_write_eod_database: false` in `config.yaml` turns it off.
>   **Your own settings file decides first:** v1.2.0 wrote `false` into it the first time
>   it ran, and a value already there wins over the new default. Set it to `true` in
>   `~/.nse_bse_downloader/user_preferences.json`, or delete that file, if you want the
>   database.
> - **The symbol-history stage has a section of its own, and its progress tells the truth.**
>
> **Coming from v1.1.1 or older?** Everything in
> [v1.2.0](https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.2.0) is in this
> release as well — read those notes too.

## Downloads

| Your system | File |
|---|---|
| Windows 10/11, 64-bit | `NSE_BSE_Downloader-1.2.1-windows-x64.zip` |
| Mac, Apple Silicon (M1–M4) | `NSE_BSE_Downloader-1.2.1-darwin-arm64.zip` |
| Linux, 64-bit | `NSE_BSE_Downloader-1.2.1-linux-x64.zip` |

Nothing else to install — Python and all libraries are bundled. Each file has a
matching `.sha256` if you want to verify your download:

```bash
shasum -a 256 -c NSE_BSE_Downloader-1.2.1-darwin-arm64.zip.sha256
```

**Intel Macs:** no prebuilt build. Run from source — see
[UPGRADE.md](https://github.com/pparesh25/NSE_BSE_Downloader/blob/v1.2.1/UPGRADE.md).

### First launch

These builds are not signed with a paid developer certificate, so both systems will
warn you once. This is expected.

- **macOS** — "cannot be opened because it is from an unidentified developer."
  **Right-click** (or Control-click) the app → **Open** → **Open**. Once only.
- **Windows** — "Windows protected your PC." **More info** → **Run anyway**.
- **Linux** — nothing unusual.

## What's new since v1.2.0

### The database starts collecting itself

`--verify-eod-parity`, `--republish-histories` and `--snapshot-revisions` all read the
database, and they can only read what has already been written. Shipping the mirror off
meant that turning one of them on months later found an empty database and nothing to
work with. So a fresh install now mirrors from its first download, and the two settings
that read the database stay off until they have earned their own release.

The text files remain the system of record. Turning the mirror off leaves everything it
has already written in place.

### Symbol histories have their own section

The stage that builds symbol-wise files runs after every download has finished, so it no
longer sits in the download list as if it were a seventh exchange.

Its progress also runs once, across the whole stage. It settles in batches — that is what
keeps a long backfill inside memory — and the bar used to restart at zero for each of
them, eleven times for a 173-day run, with nothing on screen to say how many were left.
Each update now names the batch it is on: `Batch 3/11 · 4500/7735 symbols`.

### A delivery report that is not out yet reads as pending

NSE publishes the day's delivery file after its bhavcopy. A run started before it appears
downloads the day and leaves the two delivery columns empty, and **Retry Failed/Pending**
fills them in once the report is published. That segment now says so, in the pending
colour. Before, it turned red while its own message still read "Completed".

## Everything else

Unchanged from [v1.2.0](https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.2.0),
including the certificate store every build carries.

## Requirements

Python 3.10 or newer for source installs. The prebuilt applications need nothing
installed.
