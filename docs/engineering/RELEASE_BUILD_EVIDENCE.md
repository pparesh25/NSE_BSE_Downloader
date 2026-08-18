# Release build evidence

Date: 2026-08-18 (Asia/Kolkata)
Tag: `v1.1.1`
Source commit built: `354b6180faab85535c5fd0843b52f5d12ce591a5`
Release status: **built and verified; publication pending**

## Purpose

This record describes the artifacts that were actually shipped. An integrity record
pinned to a superseded commit is worse than none, because it invites trust in a stale
hash, so this file is rewritten at each release against the tagged commit rather than
appended to. What it replaces is summarized at the end.

## What was built

GitHub Actions run [`32177266322`](https://github.com/pparesh25/NSE_BSE_Downloader/actions/runs/32177266322),
triggered by the `v1.1.1` tag push. Three isolated Python 3.13 Nuitka builds, each
installing only `requirements.txt` and `requirements-build.txt` and asserting that
pytest and mypy are absent:

| Target | Runner | Wall clock |
| --- | --- | ---: |
| macOS 15 ARM64 | `macos-15` | 18 min 41 s |
| Ubuntu 24.04 x64 | `ubuntu-24.04` | 15 min 36 s |
| Windows Server 2022 x64 | `windows-2022` | 27 min 42 s |

The Quality Gates run for the same commit also passed: tests on Python 3.10 and 3.13,
Ruff, mypy, and the packaging dry run.

## Built assets

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `NSE_BSE_Downloader-1.1.1-darwin-arm64.zip` | 65,356,710 | `b3f1fe0da90207c0e0ed9830c2b209af94351c1c28d47fccd4721dc1768e2f88` |
| `NSE_BSE_Downloader-1.1.1-windows-x64.zip` | 48,543,930 | `c33428b7a8cf4a47ccf4320eb75548ed2de90c6ffd0a716884f93a57b0182d4d` |
| `NSE_BSE_Downloader-1.1.1-linux-x64.zip` | 84,643,953 | `53d10708c0ad3d71c53ffc16696c894db728296a4145af322360b0716a497004` |

Each ZIP ships with its `.sha256` sidecar in `shasum -c` format, so a user can verify
a download without trusting this document.

## Independent verification

Performed against the downloaded files, not inferred from a green tick.

- **Checksums.** All three sidecars verified with `shasum -a 256 -c`: `OK`.
- **Provenance.** Every `BUILD-METADATA.json` records `version 1.1.1`,
  `git_commit 354b6180faab…` — the tagged commit — and `trust_status: unsigned`.
- **Each build can open a TLS connection.** This is the check the previous release did
  not have. Every packaged binary completed one real verified HTTPS request during
  packaging, and each resolved its certificate bundle from *inside* the artifact rather
  than from the machine that built it:

  | Platform | Bundle the packaged binary loaded | Authorities |
  | --- | --- | ---: |
  | macOS | `…/main.app/Contents/MacOS/certifi/cacert.pem` | 121 |
  | Linux | `/tmp/onefile_…/certifi/cacert.pem` | 121 |
  | Windows | `…\Temp\onefile_…\certifi\cacert.pem` | 121 |

- **On a machine that is not the build machine.** The macOS asset was downloaded,
  extracted and run on the owner's Mac — the same Mac where the withdrawn v1.1.0 build
  failed. `--verify-tls` passed with 121 authorities loaded from inside the app bundle.
  The same Mac still reproduces `CERTIFICATE_VERIFY_FAILED` with the withdrawn build,
  which is what makes this a controlled comparison rather than an assertion.
- **The packaged application starts.** The macOS bundle was run with `--smoke-gui`
  under a temporary `HOME` and an isolated data root, so it could not reach the real
  one: **exit code 0**, and it created its folders under the temporary root only.

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

1. Application icons for macOS, Windows and Linux.
2. Signing credentials: Developer ID plus notarization for macOS, and a Windows
   certificate if that route is chosen.
3. A macOS Intel build, or a documented source-only route for Intel hardware.
4. Automating the Release step itself: both workflows still end at
   `actions/upload-artifact`, so publishing is still manual.

## Superseded evidence

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
previous revision — checksums, layout, provenance, Gatekeeper, `--smoke-gui` — is
satisfied by a build that cannot open a TLS connection, because none of them touched
the network. The one property a user cares about, that the application can fetch a
bhavcopy, was never asserted against a compiled artifact on any platform. That is the
gap `--verify-tls` closes, and it is why this file now leads with it.

Running the artifact on a real machine also proved worth doing for its own sake: it
found that the new check misread an HTTP 429 rate limit as a certificate failure, which
would have failed later release builds at random. Fixed in PR #15 before this release.

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
