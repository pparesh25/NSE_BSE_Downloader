# Release build evidence

Date: 2026-09-15 (Asia/Kolkata)
Tag: `v1.2.0`
Source commit built: `28c6d16b129f8158e0e048f52251caa63dc6505f`
Release status: **published 2026-09-15 as the Latest GitHub Release**; `main` still reads
1.1.1, so installed clients are not told until PR #22 merges

## Purpose

This record describes the artifacts that were actually shipped. An integrity record
pinned to a superseded commit is worse than none, because it invites trust in a stale
hash, so this file is rewritten at each release against the tagged commit rather than
appended to. What it replaces is summarized at the end.

## What was built

GitHub Actions run [`34957597544`](https://github.com/pparesh25/NSE_BSE_Downloader/actions/runs/34957597544),
triggered by the `v1.2.0` tag push. Three isolated Python 3.13 Nuitka builds, each
installing only `requirements.txt` and `requirements-build.txt` and asserting that
pytest and mypy are absent:

| Target | Runner | Wall clock |
| --- | --- | ---: |
| macOS 15 ARM64 | `macos-15` | 16 min 26 s |
| Ubuntu 24.04 x64 | `ubuntu-24.04` | 18 min 50 s |
| Windows Server 2022 x64 | `windows-2022` | 28 min 50 s |

The Quality Gates run for the same commit also passed,
[`34957120636`](https://github.com/pparesh25/NSE_BSE_Downloader/actions/runs/34957120636):
tests on Python 3.10 and 3.13, Ruff, mypy, and the packaging dry run.

**Why this commit.** The release commit was reviewed in PR #22 as `ef1f6ea`, stacked on the
Phase 5 branch. Once PR #21 had merged, it was replayed onto `main` as `28c6d16`. The two
commits share one tree object, so not a byte of the release changed; only the parent did,
which keeps the tag reachable from `main` after PR #22 merges.

## Built assets

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `NSE_BSE_Downloader-1.2.0-darwin-arm64.zip` | 65,971,449 | `d5fa9a0d08a3e0699baca491308d6ce0af6d96dd99e9dfb82d4018002bd013bc` |
| `NSE_BSE_Downloader-1.2.0-windows-x64.zip` | 48,858,111 | `f91bd4d1e0c2b72614529b9b44a4c5fb8acbf1d47e00ecb1e301016d7b5d5656` |
| `NSE_BSE_Downloader-1.2.0-linux-x64.zip` | 85,155,788 | `2bcb7c3b2b61d5aca73fb39977c84298e6c07b46f18e0fa7ac2a942921a4b62d` |

Each ZIP ships with its `.sha256` sidecar in `shasum -c` format, so a user can verify
a download without trusting this document.

## Independent verification

Performed against downloaded files, not inferred from a green tick. The Release stayed a
draft until these checks had passed; only the anonymous download check below came after
publishing.

- **Checksums, on both copies.** All three sidecars verified with `shasum -a 256 -c` on the
  files downloaded from the workflow run, and again on the files downloaded back from the
  Release while it was still a draft: `OK`. The six Release files are byte-identical to the
  workflow's (`cmp`), so what was verified is what users download.
- **Archive integrity and layout.** Every member passes its CRC check (`unzip -t`), each
  archive holds a single top-level folder named after itself, and each executable carries
  its platform's magic number: Mach-O `cffaedfe`, PE `4d5a`, ELF `7f454c46`.
- **Provenance.** Every `BUILD-METADATA.json` records `version 1.2.0`,
  `git_commit 28c6d16b…` — the tagged commit — and `trust_status: unsigned`. The macOS
  bundle's `Info.plist` names `com.github.pparesh25.NSEBSEDownloader` at version `1.2.0`,
  with its `icon.icns`.
- **Each build can open a TLS connection.** Every packaged binary completed one real
  verified HTTPS request during packaging, loading its certificate bundle from *inside*
  the artifact:

  | Platform | Bundle the packaged binary loaded | Authorities |
  | --- | --- | ---: |
  | macOS | `…/main.app/Contents/MacOS/certifi/cacert.pem` | 121 |
  | Linux | `/tmp/onefile_…/certifi/cacert.pem` | 121 |
  | Windows | `…\Temp\onefile_…\certifi\cacert.pem` | 121 |

- **macOS, away from the build machine and with nothing inherited.** The asset downloaded
  from the Release was extracted with `ditto`, as Finder extracts it, and run on the owner's
  Mac under `env -i`: no conda or other Python on `PATH`, no `SSL_CERT_FILE`, an empty
  `HOME`. `--verify-tls` passed with 121 authorities loaded from inside the app bundle.
  `--smoke-gui` with the bundled configuration exited 0 and created its data root, its
  `SCHEMA.json` markers and its log under the empty `HOME` only; the log opens with the
  version and build date, the platform, `Packaged build: True` and the certificate bundle,
  as the release notes say it does. The application was also started with its window under
  a second empty profile, where it ran its update check and created its folders, and was
  then terminated. `codesign` reports an ad-hoc signature and Gatekeeper rejects the
  bundle, which is what the release notes' first-launch steps prepare users for.
- **Linux, on a machine that is not a GitHub runner.** A new Ubuntu 24.04.4 virtual machine
  (Lima on Apple Virtualization, no host folder mounted) ran the x86-64 asset from the
  Release through Rosetta. Because the guest itself is arm64, the x86-64 counterparts of
  libraries an Ubuntu desktop already has were added from Ubuntu's archive: `libc6`,
  `zlib1g`, `libstdc++6`, and the EGL/GL, fontconfig, FreeType, xkbcommon, GLib and D-Bus
  runtimes, 68 amd64 packages with their dependencies. With those, `--verify-tls` passed
  with 121 authorities from `…/onefile_…/certifi/cacert.pem`, and `--smoke-gui` exited 0,
  creating its data root and log under an empty `HOME`; the log reports `Linux … x86_64`
  and `Packaged build: True`. Without `zlib1g`, and then without `libstdc++6`, the binary
  could not start, which shows the build takes those two from the system. Both are present
  on any Ubuntu or Debian system with APT: `apt` depends on `libstdc++6` directly, and on
  `zlib1g` through `libapt-pkg`.
- **Windows** was not run outside GitHub's runner. Its evidence is the packaging-time
  `--verify-tls` above and `--smoke-gui`, which the packaging step of that same build ran
  on the Windows binary before it created the archive.
- **A real download, and the packaged audit reading it.** A download through the packaged
  application's window was not performed. Instead the tagged source ran the application's
  own `DownloadWorker` on its own thread, with a running event loop and an empty `HOME`,
  for 2026-09-10 and 2026-09-11 across all six segments with every append option on. Every
  segment reported success, in 32 seconds. Each daily file has its documented width — 11
  columns for equity, SME and futures, 9 for indices — and every folder carries its
  `SCHEMA.json`. 7,926 symbol histories were written, every line 14 columns wide, and
  RELIANCE's second day carries the first day's close as its `PREV_CLOSE`. The
  symbol-history stage reported its own progress, 101 updates from `0/7926` to
  `7926/7926`. No `eod.sqlite3` was created, so the database mirror is off as shipped. The
  **packaged** macOS build's `--audit` then read that tree: all 22 recorded digests
  verified and 15,451 (symbol, date) pairs checked against their snapshots, with no errors
  and six warnings, each a `head-gap` noting that nothing was downloaded between the
  configured start date and 2026-09-10 — what a two-day tree must report.
- **From the public Release page, without credentials.** After publishing, all six files
  were downloaded again anonymously: every checksum `OK`, every file byte-identical to the
  copies verified above, and `releases/latest` redirects to `v1.2.0`.
- **The owner's data was not touched.** Before any of this, the size and modification time
  of all 8,212 files under `~/NSE_BSE_Data` and `~/.nse_bse_downloader` were recorded.
  After every run above they were unchanged.

## Trust status

Shipped **unsigned**, deliberately. The reasoning is recorded in
[RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md) §1.4 and
[RELEASE_TRUST_ASSETS.md](RELEASE_TRUST_ASSETS.md): no Developer ID or Windows
signing credentials exist, and the one-time right-click→Open and SmartScreen steps are
documented for users rather than hidden.

`__update_artifacts__` in `version.py` is deliberately left empty, which keeps the
updater **notification-only** for every platform. It never executes a downloaded
artifact without a checksum bound in advance.

## What remains, after this release

1. Signing credentials: Developer ID plus notarization for macOS, and a Windows
   certificate if that route is chosen.
2. A macOS Intel build, or a documented source-only route for Intel hardware.
3. Automating the Release step itself: both workflows still end at
   `actions/upload-artifact`, so publishing is still manual.
4. A run of the Windows build on a machine that is not a GitHub runner.
5. Which of the Linux desktop libraries above the build strictly needs. They were added
   together, so only `zlib1g` and `libstdc++6` are proven requirements.

The previous revision also listed application icons. They were already embedded: the
build passes an icon for all three platforms, and the macOS bundle above carries its
`icon.icns`. That item is closed.

## Superseded evidence

### v1.1.1 — published 2026-08-19 (Asia/Kolkata), still downloadable

Built from tag `v1.1.1` at commit `354b618`, run `32177266322`, all three platforms green;
checksums and provenance verified, and `--verify-tls` passed on every packaged binary
during packaging and again on the owner's Mac. It remains on the Releases page, so its
digests stay here for anyone checking an older download:

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `NSE_BSE_Downloader-1.1.1-darwin-arm64.zip` | 65,356,710 | `b3f1fe0da90207c0e0ed9830c2b209af94351c1c28d47fccd4721dc1768e2f88` |
| `NSE_BSE_Downloader-1.1.1-windows-x64.zip` | 48,543,930 | `c33428b7a8cf4a47ccf4320eb75548ed2de90c6ffd0a716884f93a57b0182d4d` |
| `NSE_BSE_Downloader-1.1.1-linux-x64.zip` | 84,643,953 | `53d10708c0ad3d71c53ffc16696c894db728296a4145af322360b0716a497004` |

### v1.1.0 — published 2026-08-09, withdrawn 2026-08-14

Built from tag `v1.1.0` at commit `059f497`, run `31274716003`, all three platforms
green, checksums verified, packaged macOS app launched. It was withdrawn with all six
assets because the builds could not download anything.

The bundle shipped its own `libssl.3` and `libcrypto.3`, compiled with
`OPENSSLDIR = /Library/Frameworks/Python.framework/Versions/3.13/etc/openssl` — the
runner's python.org framework path, absent on a user's machine — and no `cacert.pem`
was included, because `certifi` was not a dependency. The packaged application
therefore started with zero trusted roots. Every HTTPS request failed certificate
verification, the downloader correctly treated that as terminal, and the host circuit
breaker then skipped the remaining dates. Running from source was never affected.

Its digests are not repeated here: those files are not downloadable and an integrity
record for a withdrawn artifact invites exactly the mistaken trust this file exists to
prevent. The commit history holds them if they are ever needed.

**Why the verification of that release did not catch it.** Every check in this file's
revision at the time — checksums, layout, provenance, Gatekeeper, `--smoke-gui` — is
satisfied by a build that cannot open a TLS connection, because none of them touched
the network. The one property a user cares about, that the application can fetch a
bhavcopy, was never asserted against a compiled artifact on any platform. That is the
gap `--verify-tls` closes, and it is why this file leads with it.

Running the artifact on a real machine also proved worth doing for its own sake: it
found that the new check misread an HTTP 429 rate limit as a certificate failure, which
would have failed later release builds at random. Fixed in PR #15 before v1.1.1.

### Earlier rehearsals

Two records are retained in summary only, because their hashes describe artifacts that
were never published:

- **Local macOS ARM64 build, commit `4d25c17e`, 2026-08-05.** Built from the primary
  development environment, so it pulled in mypy, pydantic, PyObjC, openpyxl, lxml and
  Pillow; its 391 MiB bundle was never a valid estimate for a clean artifact. It did
  establish the archive contract — single top-level folder, deterministic member
  ordering and timestamps, `BUILD-METADATA.json`, preserved executable mode, no
  symlinks — and it found that Python's standard ZIP extraction discards the
  executable bit, which is why the updater restores archived executable bits (and only
  those) after all path, link, size and compression checks pass.
- **Clean matrix rehearsal, run `30948814245`, commit `9dba2aca`, and the tag-less
  rehearsal `31054912467`.** All three platforms green; artifacts audited for CRC
  integrity, single-root layout, sidecar checksum, embedded version, exact source
  commit, executable mode, native binary magic (`cffaedfe`, `4d5a`, `7f454c46`) and
  clean dependencies — 21 runtime distributions, none of them development-only. An
  earlier matrix attempt was rejected during provenance review because `GITHUB_SHA`
  identified GitHub's temporary PR merge commit; the workflow and packager were
  corrected to use `SOURCE_COMMIT` before that run.
