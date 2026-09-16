# Release build evidence

Date: 2026-09-16 (Asia/Kolkata)
Tag: `v1.2.1`
Source commit built: `abb639eab05f216a8afca92fab968f114f658734`
Release status: **published 2026-09-16 as the Latest GitHub Release**; `main` still reads
1.1.1, so installed clients are not told until PR #22 merges

## Purpose

This record describes the artifacts that were actually shipped. An integrity record
pinned to a superseded commit is worse than none, because it invites trust in a stale
hash, so this file is rewritten at each release against the tagged commit rather than
appended to. What it replaces is summarized at the end.

## Why there are two releases a day apart

v1.2.0 was built, verified and published on 2026-09-15. The owner then installed it on a
clean profile and ran 2026-01-01 to 2026-09-15 across all six segments. It published all
173 daily files in every segment — and it never collected the database the release
advertises, because `dual_write_eod_database` shipped off and the three commands that read
the database can only read what the mirror has already written.

Turning that default on changes what a *downloaded application* does, so it needs a build.
The two display problems the same run exposed — the symbol-history bar restarting once per
journal batch, and a pending delivery report rendered as an error — went into the same
build, which is what the owner had asked for when they were recorded.

## What was built

GitHub Actions run [`35055371418`](https://github.com/pparesh25/NSE_BSE_Downloader/actions/runs/35055371418),
triggered by the `v1.2.1` tag push. Three isolated Python 3.13 Nuitka builds, each
installing only `requirements.txt` and `requirements-build.txt` and asserting that
pytest and mypy are absent:

| Target | Runner | Wall clock |
| --- | --- | ---: |
| macOS 15 ARM64 | `macos-15` | 15 min 39 s |
| Ubuntu 24.04 x64 | `ubuntu-24.04` | 19 min 00 s |
| Windows Server 2022 x64 | `windows-2022` | 23 min 44 s |

The Quality Gates run for the same commit also passed,
[`35055193215`](https://github.com/pparesh25/NSE_BSE_Downloader/actions/runs/35055193215):
tests on Python 3.10 and 3.13, Ruff, mypy, and the packaging dry run. Locally, on the same
tree: 606 passed in both interpreters, coverage 79.24%, `ruff` and `mypy` (63 files) clean,
`--smoke-gui` exit 0.

## Built assets

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `NSE_BSE_Downloader-1.2.1-darwin-arm64.zip` | 65,988,791 | `c6295a804d4d44405df490a7b02a7028f89577883a5026eb7cf437c0389ad10f` |
| `NSE_BSE_Downloader-1.2.1-windows-x64.zip` | 48,875,678 | `cbd157667c194b935971d36533adc49a768ff16d84786814aea20dba77db9445` |
| `NSE_BSE_Downloader-1.2.1-linux-x64.zip` | 85,278,983 | `935e8f41aec25a61f4c913a2a9eceeb6849dae71ebab8f242b7b5ab0d16f64d1` |

Each ZIP ships with its `.sha256` sidecar in `shasum -c` format, so a user can verify
a download without trusting this document.

## Independent verification

Performed against downloaded files, not inferred from a green tick. The Release stayed a
draft until these checks had passed; only the anonymous download check below came after
publishing.

- **Checksums, on three copies.** All three sidecars verified with `shasum -a 256 -c` on
  the files downloaded from the workflow run, on the files downloaded back from the
  Release while it was still a draft, and on the files downloaded from the public page
  afterwards without credentials. All `OK`, and all three copies are byte-identical
  (`cmp`), so what was verified is what users download. `releases/latest` redirects to
  `v1.2.1`.
- **Archive integrity and layout.** Every member passes its CRC check (`unzip -t`), each
  archive holds a single top-level folder named after itself, and each executable carries
  its platform's magic number: Mach-O `cffaedfe`, PE `4d5a`, ELF `7f454c46`.
- **Provenance.** Every `BUILD-METADATA.json` records `version 1.2.1`,
  `git_commit abb639ea…` — the tagged commit — and `trust_status: unsigned`. The macOS
  bundle's `Info.plist` names `com.github.pparesh25.NSEBSEDownloader` at version `1.2.1`,
  with its `icon.icns`.
- **Each build can open a TLS connection.** Every packaged binary completed one real
  verified HTTPS request during packaging, loading its certificate bundle from *inside*
  the artifact:

  | Platform | Bundle the packaged binary loaded | Authorities |
  | --- | --- | ---: |
  | macOS | `…/main.app/Contents/MacOS/certifi/cacert.pem` | 121 |
  | Linux | `/tmp/onefile_…/certifi/cacert.pem` | 121 |
  | Windows | `…\Temp\onefile_…\certifi\cacert.pem` | 121 |

- **The change this release exists for, end to end.** With nothing turned on by hand, the
  tagged source downloaded 2026-09-10 and 2026-09-11 across all six segments into an empty
  `HOME`: every segment succeeded, and `.state/eod.sqlite3` appeared by itself —
  3,821,568 bytes, 17,229 rows over both dates (BSE EQ 8,694, BSE INDEX 154, NSE EQ 5,822,
  NSE FO 1,294, NSE INDEX 330, NSE SME 935) and 12 published frames. The **packaged**
  macOS build then read that database back:

  ```
  Checked 12 published daily file(s) and 7926 symbol history/histories.
  Every mirrored file regenerated byte for byte.
  ```

  `--verify-eod-parity` exited 0, left the database byte-identical, and left no `-shm` or
  `-wal` behind. The symbol stage reported itself as `Batch 1/1 · 7926/7926 symbols`.
- **macOS, away from the build machine and with nothing inherited.** The asset downloaded
  from the Release was extracted with `ditto`, as Finder extracts it, and run on the
  owner's Mac under `env -i`: no conda or other Python on `PATH`, no `SSL_CERT_FILE`, an
  empty `HOME`. `--verify-tls` passed with 121 authorities loaded from inside the app
  bundle, and `--smoke-gui` exited 0. **The settings file that fresh profile was given
  carries `dual_write_eod_database: true`**, with the two settings that read the database
  false — which is the whole point of this release, checked in the shipped application
  rather than in the source. Its log opens with `1.2.1 (build 2026-09-16)`,
  `Packaged build: True` and the bundle path. `codesign` reports an ad-hoc signature and
  Gatekeeper rejects the bundle, which is what the release notes' first-launch steps
  prepare users for.
- **Linux, on a machine that is not a GitHub runner.** A fresh Ubuntu 24.04.4 virtual
  machine (Lima on Apple Virtualization, no host folder mounted) ran the x86-64 asset from
  the Release through Rosetta. Because the guest is arm64, the x86-64 counterparts of
  libraries an Ubuntu desktop already carries were installed from Ubuntu's archive — 66
  amd64 packages, none of them from the build. `--verify-tls` passed with 121 authorities
  from `…/onefile_…/certifi/cacert.pem`, `--smoke-gui` exited 0, and the settings file
  there also carries `dual_write_eod_database: true`. The v1.2.0 evidence established that
  the Linux build takes `zlib1g` and `libstdc++6` from the system; both come with APT on
  every Ubuntu and Debian.
- **Windows** was not run outside GitHub's runner. Its evidence is the packaging-time
  `--verify-tls` above and `--smoke-gui`, which the packaging step ran on the Windows
  binary before it created the archive.
- **The owner's data was not touched.** Every run above used an isolated `HOME` under the
  session's scratch directory. Nothing under `~/NSE_BSE_Data` or `~/.nse_bse_downloader`
  was written during this work: both trees hold zero paths modified after this session
  began.

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
5. Which of the Linux desktop libraries the build strictly needs. They were installed
   together, so only `zlib1g` and `libstdc++6` are proven requirements.
6. A download driven through the packaged application's own window. The real download
   above ran the same `DownloadWorker` from the tagged source, and the packaged build
   verified what it wrote, but no test here drove the shipped GUI itself.

## Superseded evidence

### v1.2.0 — published 2026-09-15, superseded by v1.2.1 the next day, still downloadable

Built from tag `v1.2.0` at commit `28c6d16`, run
[`34957597544`](https://github.com/pparesh25/NSE_BSE_Downloader/actions/runs/34957597544),
all three platforms green in 16 min 26 s, 18 min 50 s and 28 min 50 s. Verified the same
way as above: checksums on three copies, archive layout, provenance, `--verify-tls` with
121 authorities on every platform, a clean-profile run on the owner's Mac, a fresh Ubuntu
VM through Rosetta, and a real two-day download read back by the packaged `--audit` — all
22 recorded digests verified and 15,451 (symbol, date) pairs checked, with no errors.

What it lacked was the point of v1.2.1: the database it advertised was never collected,
because the mirror shipped off. The release remains on the Releases page, so its digests
stay here for anyone checking an older download:

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `NSE_BSE_Downloader-1.2.0-darwin-arm64.zip` | 65,971,449 | `d5fa9a0d08a3e0699baca491308d6ce0af6d96dd99e9dfb82d4018002bd013bc` |
| `NSE_BSE_Downloader-1.2.0-windows-x64.zip` | 48,858,111 | `f91bd4d1e0c2b72614529b9b44a4c5fb8acbf1d47e00ecb1e301016d7b5d5656` |
| `NSE_BSE_Downloader-1.2.0-linux-x64.zip` | 85,155,788 | `2bcb7c3b2b61d5aca73fb39977c84298e6c07b46f18e0fa7ac2a942921a4b62d` |

### v1.1.1 — published 2026-08-19 (Asia/Kolkata), still downloadable

Built from tag `v1.1.1` at commit `354b618`, run `32177266322`, all three platforms green;
checksums and provenance verified, and `--verify-tls` passed on every packaged binary
during packaging and again on the owner's Mac. Its digests:

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
