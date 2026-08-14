# Release build evidence

Date: 2026-08-09 (Asia/Kolkata)
Tag: `v1.1.0`
Source commit built and published: `059f497c7716f9910ea70a2b8ea0e1d2dcb769d4`
Release status: **withdrawn 2026-08-14.** Published on 2026-08-09, then unpublished
with all six assets after the shipped builds proved unable to download any market
data. The `v1.1.0` tag remains; the Release does not, so
<https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.1.0> now shows the
tag and its source archives only. See "Why the release was withdrawn" below.

## Purpose

This record describes the artifacts that were actually shipped. An integrity record
pinned to a superseded commit is worse than none, because it invites trust in a stale
hash, so this file is rewritten at each release against the tagged commit rather than
appended to. The superseded rehearsal evidence it replaces is summarized at the end.

## What was built

GitHub Actions run [`31274716003`](https://github.com/pparesh25/NSE_BSE_Downloader/actions/runs/31274716003),
triggered by the `v1.1.0` tag push. Three isolated Python 3.13 Nuitka builds, each
installing only `requirements.txt` and `requirements-build.txt` and asserting that
pytest and mypy are absent:

| Target | Runner | Wall clock |
| --- | --- | ---: |
| macOS 15 ARM64 | `macos-15` | 18 min 30 s |
| Ubuntu 24.04 x64 | `ubuntu-24.04` | 20 min 23 s |
| Windows Server 2022 x64 | `windows-2022` | 27 min 18 s |

The Quality Gates run for the same commit also passed: tests on Python 3.10 and 3.13,
Ruff, mypy, and the packaging dry run.

## Published assets

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `NSE_BSE_Downloader-1.1.0-darwin-arm64.zip` | 65,188,287 | `178ed931997321151248a7e7fe38d61e7e7491e4ade12a00ddd6d7c74c93eb65` |
| `NSE_BSE_Downloader-1.1.0-windows-x64.zip` | 48,394,086 | `660b0f1c521616778dd79c19925aa06fb0f1d4c7ff36301d614d9ef9dcebd0ab` |
| `NSE_BSE_Downloader-1.1.0-linux-x64.zip` | 84,434,022 | `bacafebe4695861fc7c8aa519d4327212285c8a0b8f154371f1e10f061bbccc3` |

Each ZIP shipped with its `.sha256` sidecar in `shasum -c` format, so a user could
verify a download without trusting this document. The assets are no longer
downloadable; the digests are kept so a copy already downloaded can still be
identified as one of these builds.

## Why the release was withdrawn

The three published applications could not download market data at all. Every date in
a run failed, the log reported an SSL certificate issue for each one, and the host
circuit breaker then opened and skipped the rest.

Reproduced on 2026-08-14 against the withdrawn `darwin-arm64` asset, whose
`BUILD-METADATA.json` records `git_commit 059f497` — the tagged commit in this record:

- The bundle ships its own `libssl.3.dylib` and `libcrypto.3.dylib`, compiled with
  `OPENSSLDIR = /Library/Frameworks/Python.framework/Versions/3.13/etc/openssl`. That
  is the python.org framework path present on the build runner; it does not exist on
  an end user's machine.
- No `cacert.pem` is bundled anywhere in the app, and `certifi` is not a dependency,
  so the bundled OpenSSL starts with **zero trusted roots**.
- Run as shipped, the packaged application fails its own update check with
  `CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`.
- The same binary, with `SSL_CERT_FILE=/etc/ssl/cert.pem`, completes that check
  silently. Nothing else changed between the two runs.

Classifying a certificate failure as terminal is correct, so the downloader behaved as
designed; the trust store was simply absent. Running from source is unaffected,
because it uses the certificates the user's own Python installation already trusts.

**Why the verification below did not catch it.** Every check in this record is
satisfied by a build that cannot open a TLS connection. `--smoke-gui` constructs the
GUI and exits; it makes no network request. Checksums, layout, provenance and
Gatekeeper all describe the file, not its ability to work. The one property a user
cares about — that it can fetch a bhavcopy — was never asserted against a compiled
artifact on any platform.

The macOS asset is the one reproduced against. The Windows build almost certainly
carries the same defect; the Linux build may find `/etc/ssl/certs` and survive. Each
platform must be verified separately rather than inferred from this one.

## Independent verification

Performed against the downloaded files, not inferred from a green tick.

- **Checksums.** All three sidecars verified with `shasum -a 256 -c`: `OK`.
- **Layout.** Each archive has exactly one top-level directory, named after the
  archive itself.
- **Provenance.** Every `BUILD-METADATA.json` records `version 1.1.0`,
  `git_commit 059f497c7716…` — the tagged commit — and `trust_status: unsigned`.
- **The packaged application starts.** The macOS bundle was extracted and run with
  `--smoke-gui` under a temporary `HOME`, so it could not reach the real data root:
  **exit code 0**. It created its folders under the temporary home only.
- **The single-instance lock works in the packaged build.** `.state/app.lock` was
  created during the run and cleared on exit, which exercises the Phase 3.5 guard in a
  compiled binary rather than only under pytest.
- **Gatekeeper.** `codesign -dv` reports `Signature=adhoc`, `TeamIdentifier=not set`,
  and `spctl --assess --type execute` reports `rejected`. This is the documented,
  expected behaviour for an unsigned build and is exactly what `UPGRADE.md` and the
  release notes prepare users for.
- **From the outside.** Every asset was downloaded again from the published Release
  page — not from the build directory — and re-verified against the sidecars that came
  down with them.

## Trust status

Shipped **unsigned**, deliberately. The reasoning is recorded in
[RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md) §1.4 and
[RELEASE_TRUST_ASSETS.md](RELEASE_TRUST_ASSETS.md): no Developer ID or Windows
signing credentials exist, and the one-time right-click→Open and SmartScreen steps are
documented for users rather than hidden.

`__update_artifacts__` in `version.py` is deliberately left empty, which keeps the
updater **notification-only** for every platform. It never executes a downloaded
artifact without a checksum bound in advance.

## What remains, before anything is published again

1. **A certificate store in the packaged build**, and a check that proves it. Nothing
   may be republished until a compiled artifact on each platform is observed to
   complete a real HTTPS request. Tracked in
   [PENDING_TASKS.md](PENDING_TASKS.md) §3.
2. Application icons for macOS, Windows and Linux.
3. Signing credentials: Developer ID plus notarization for macOS, and a Windows
   certificate if that route is chosen.
4. A macOS Intel build, or a documented source-only route for Intel hardware.
5. Automating the Release step itself: both workflows still end at
   `actions/upload-artifact`, so publishing was manual this time.

## Superseded evidence

Two earlier records are retained here in summary only, because their hashes describe
artifacts that were never published:

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
