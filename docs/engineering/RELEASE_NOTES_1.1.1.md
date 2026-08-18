# v1.1.1

> ### If you installed a v1.1.0 prebuilt application, replace it with this one.
> Those builds could not download anything. They shipped without a certificate store,
> so every date failed with an "SSL certificate issue" and the app then stopped trying
> that exchange for a while. The exchanges were fine; the build was not. They have been
> withdrawn. Your data folder is untouched — those builds could not write to it at all.
>
> **If you run from source, you were never affected.** Source installs use the
> certificates your own Python already trusts. Upgrading is still worth it: run
> `pip install -r requirements.txt` again, because `certifi` is now a dependency.

## Downloads

| Your system | File |
|---|---|
| Windows 10/11, 64-bit | `NSE_BSE_Downloader-1.1.1-windows-x64.zip` |
| Mac, Apple Silicon (M1–M4) | `NSE_BSE_Downloader-1.1.1-darwin-arm64.zip` |
| Linux, 64-bit | `NSE_BSE_Downloader-1.1.1-linux-x64.zip` |

Nothing else to install — Python and all libraries are bundled. Each file has a
matching `.sha256` if you want to verify your download:

```bash
shasum -a 256 -c NSE_BSE_Downloader-1.1.1-darwin-arm64.zip.sha256
```

**Intel Macs:** no prebuilt build. Run from source — see
[UPGRADE.md](https://github.com/pparesh25/NSE_BSE_Downloader/blob/v1.1.1/UPGRADE.md).

### First launch

These builds are not signed with a paid developer certificate, so both systems will
warn you once. This is expected.

- **macOS** — "cannot be opened because it is from an unidentified developer."
  **Right-click** (or Control-click) the app → **Open** → **Open**. Once only.
- **Windows** — "Windows protected your PC." **More info** → **Run anyway**.
- **Linux** — nothing unusual.

## What was wrong

A compiled build carries its own copy of OpenSSL. That copy was compiled to look for
certificate authorities in a directory that exists on the machine that builds the
release and on no user's machine, and no certificate bundle was included in the
application. So the packaged application started with nothing to verify against, and
every HTTPS request to NSE and BSE failed certificate verification.

The downloader treats a certificate failure as final rather than retrying it, which is
correct — retrying cannot fix a broken trust chain. After a few such failures it also
paused requests to that host. The visible result was an "SSL certificate issue" for
every date, then skipped dates, and nothing downloaded.

## What changed

**The application carries its own certificate authorities.** They travel inside the
build, so a packaged application trusts exactly what a source install trusts, on any
machine. Certificate verification itself was never weakened and is not weakened now.

**One trust store for every request.** The corporate-action endpoints had been opening
connections with the default settings instead of the application's own; they now go
through the same path as everything else.

**Packaging proves it before publishing.** A compiled build must now complete one real
verified HTTPS request during packaging, and a build with no certificate bundle fails
the packaging check outright. Every check that existed before this release — checksums,
archive layout, build provenance, startup, code-signing status — was satisfied by a
build that could not open a single connection. That gap is what allowed v1.1.0 to ship,
and it is closed.

## Everything else

Unchanged from [v1.1.0](https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.1.0).
Its notes describe the unified date-aware downloads, the nine-column output contracts,
symbol-wise histories, corporate-action adjustment and the rest of that release, and all
of it applies here. Data written by v1.1.0 from source needs no migration.

## Requirements

Python 3.10 or newer for source installs. The prebuilt applications need nothing
installed.
