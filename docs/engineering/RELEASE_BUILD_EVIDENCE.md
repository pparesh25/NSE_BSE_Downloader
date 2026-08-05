# Release build evidence

Date: 2026-08-05 (Asia/Kolkata)
Branch: `codex/pyside6-port-v1.1.0`
Source commit tested: `4d25c17e836359a705182aaf0176434d37235110`
Release status: blocked; no release or update notification has been published

## Purpose

This record separates build-readiness evidence from release authorization. A build
may pass compilation and smoke tests without being suitable for community release.
The canonical `main` branch, v1.1 tag, release assets, and update metadata remain
unchanged until all required platform and trust gates pass.

## Local macOS ARM64 build

- Host: macOS 26.5.2 ARM64.
- Python: conda-forge CPython 3.13.12 from `opentrader313`.
- Compiler: Apple clang 21.0.0.
- Nuitka: 4.1rc5.
- Command: `build_nuitka_cross_platform.py --target-platform=darwin --build
  --clean --standalone-folder --output-dir=dist-macos-arm64`.
- Result: successful `app-dist` build for macOS 11.0 ARM64 or newer.
- Bundle identity: `com.github.pparesh25.NSEBSEDownloader`.
- Bundle version: `1.1.0`.
- Embedded resources: `config.yaml` and `src/gui/resources/QR_UPI.jpeg` present.
- Bundle size: 391 MiB.
- Main executable SHA-256:
  `669d45d7c7fbbd720030b85c7fa967a2abfec904e9bfc84795a1ac0fed9957d5`.
- Direct packaged `--help` smoke test: passed with exit code 0 before configuration
  or user-data initialization.

The build is intentionally not a release candidate. The existing primary environment
contains development and unrelated optional packages. The compilation report tracked
2,195 modules, including 1,445 compiled modules, and pulled in packages such as mypy,
pydantic, PyObjC, openpyxl, lxml, and Pillow. The resulting size is therefore not a
valid estimate for a clean community artifact.

## Archive and updater compatibility

The cross-platform packager created:

- Archive: `NSE_BSE_Downloader-1.1.0-darwin-arm64.zip`.
- Size: 141,433,363 bytes (approximately 145 MiB on disk).
- SHA-256:
  `ddb2eb187ef3f495f96fde3372281455d0fe9a62f82bbb4e4f1f1bcb7ad06394`.
- Contract: one top-level folder, deterministic member ordering/timestamps,
  `BUILD-METADATA.json`, preserved executable mode metadata, and no symlinks.

An end-to-end extraction test found that Python's standard ZIP extraction discarded
the executable bit. The updater now restores only archived executable bits after all
existing path, link, size, and compression checks pass. It does not restore writable,
setuid, setgid, or sticky bits. The extracted macOS executable retained mode `0755`
and passed the packaged `--help` smoke test with exit code 0.

## Trust and distribution gates

- Nuitka applied an ad-hoc signature and `codesign --verify --deep --strict` passed.
- The host has zero valid Developer ID code-signing identities.
- Gatekeeper rejected the ad-hoc signed bundle, as expected.
- Developer ID signing and Apple notarization are not complete.
- No application icon assets (`.icns`, `.ico`, or `.png`) currently exist.
- `version.py` still has blank `__update_url__` and `__update_sha256__` values.
- The local ZIP and checksum above are evidence artifacts only and must not be
  published as a release asset.

## Reproducible clean-build workflow

`.github/workflows/release-artifacts.yml` defines isolated Python 3.13 builds for:

- macOS 15 ARM64;
- Windows Server 2022 x64;
- Ubuntu 24.04 x64.

Each runner installs only `requirements.txt` and `requirements-build.txt`, asserts
that pytest and mypy are absent, performs a real Nuitka build, runs the packaged
`--help` smoke test, creates an updater-compatible ZIP plus SHA-256 sidecar, and
uploads the compilation report with a 14-day retention period. Action versions are
pinned to full commit SHAs. Pull-request builds check out and record the exact PR-head
commit rather than GitHub's temporary merge commit. The workflow does not publish a
GitHub Release, sign a binary, notarize an app, change version metadata, or merge the
migration branch.

The first matrix run passed all three functional builds but was rejected during
independent provenance review because `GITHUB_SHA` identified GitHub's temporary PR
merge commit. The workflow and packager were corrected to use `SOURCE_COMMIT` before
the accepted run described below.

## Accepted clean matrix evidence

- GitHub Actions run: `30948814245`.
- Exact source commit: `9dba2aca8179f43cb1875c3288753bfa3fc75698`.
- macOS 15 ARM64: passed in 16 minutes 25 seconds.
- Ubuntu 24.04 x64: passed in 17 minutes 48 seconds.
- Windows Server 2022 x64: passed in 26 minutes 9 seconds.
- The push and pull-request Quality Gates runs for the same source commit also passed.

The three Actions artifacts were downloaded to an isolated temporary directory and
audited independently of their workflow steps. Every inner ZIP passed CRC/integrity,
single-root layout, sidecar checksum, embedded version, exact source-commit,
executable-mode, native binary-magic, resource-report, and clean-dependency checks.

| Target | ZIP bytes | SHA-256 | Report modules |
| --- | ---: | --- | ---: |
| macOS ARM64 | 61,345,459 | `829460eaf5cd3dbcf98e4f94b8799fb2042f0bcb8f8a764ef1977791b849d053` | 1,165 |
| Windows x64 | 46,382,818 | `ec625b1bea69ae88ac65d6150df6280a9695daa9df143ccb946e378bb66e7715` | 1,150 |
| Linux x64 | 82,416,308 | `e26da32008e91fc3d6c579d9f1e86ff9e3f97b1e5d758363c6382710633c8aef` | 1,162 |

All reports contained 21 runtime distributions. None included mypy, pytest,
pydantic, pydantic-core, openpyxl, lxml, Pillow, or PyObjC. The native signatures
were Mach-O ARM64 (`cffaedfe`), Windows PE (`4d5a`), and Linux ELF (`7f454c46`).
The accepted files remain temporary Actions artifacts and are not final signed release
assets.

## Validation completed after packaging changes

- Full suite in `opentrader313`: 182 tests passed.
- Full suite in `mark_screener`: 182 tests passed.
- Coverage: 73.89%, above the required 70% gate.
- Ruff: passed.
- mypy: passed for all configured production and packaging modules.
- Darwin, Windows, and Linux packaging dry runs: passed.
- Release packager and updater security regression suite: passed.
- All data-related tests used temporary roots. `/Users/paresh/NSE_BSE_Data` was not
  modified.

## Remaining release blockers

1. Run the clean GitHub matrix and inspect all three uploaded ZIPs, checksums, sizes,
   compilation reports, and packaged smoke-test logs.
2. Decide supported release platforms and whether macOS Intel is required in addition
   to ARM64.
3. Add professional application icons for macOS, Windows, and Linux.
4. Obtain the required signing credentials. Complete Windows signing if selected and
   Developer ID signing plus notarization for macOS.
5. Test each final signed artifact on a clean machine or clean virtual machine.
6. Create the immutable v1.1.0 release and independently verify published asset
   checksums.
7. Only then populate checksum-bound update metadata, merge the migration branch, and
   publish the update notification.
