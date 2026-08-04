# Nuitka packaging preparation

## Current status

Unsigned Nuitka artifact builds have been validated on macOS ARM64, Windows
x64, and Linux x64. They are reproducible test candidates, not public releases.
The build entry point still defaults to a no-write dry run and requires
`--build` before compilation can start.

Project-owned PNG, ICO, and ICNS icons are required packaging resources. A
manual, credential-gated trusted-candidate workflow is also defined for macOS
Developer ID signing/notarization and Windows Authenticode signing. See
`RELEASE_TRUST_ASSETS.md` for credential setup and publication gates.

Supported/tested source runtimes:

- Python 3.10 (`mark_screener`)
- Python 3.13 (`opentrader313`, designated packaging environment)

## Validate without building

From an activated environment containing `requirements.txt` dependencies:

```bash
python build_nuitka_cross_platform.py --target-platform=darwin
./build_nuitka_macos.sh
```

Both commands validate the entry point, `config.yaml`, QR image, runtime
dependencies and version metadata, then print the quoted Nuitka command. They
do not install packages, clean directories or invoke Nuitka compilation.

The dry run is also covered by `tests/test_packaging_readiness.py`.
Static typing can be checked independently with `python -m mypy`; the committed
configuration passes all source modules without suppressing project errors.
The CI matrix repeats tests on Python 3.10/3.13, enforces at least 70% coverage,
and runs Ruff, mypy and the Linux packaging dry run.

## Build environment preparation

When an actual build is approved later, use the dedicated environment:

```bash
/Users/paresh/miniforge3/envs/opentrader313/bin/python -m pip install \
  -r requirements.txt -r requirements-build.txt
```

The future macOS commands are:

```bash
./build_nuitka_macos.sh --build
./build_nuitka_macos.sh --build --clean
```

`--clean` is optional and removes only explicit Nuitka output locations. The
script refuses broad project/root cleanup targets.

Use `--standalone-folder` for an inspectable `.app`/distribution folder rather
than the default app/onefile mode.

On macOS, the helper automatically repairs PySide6 plugin dependencies that
still use framework-style `@rpath` references after Nuitka creates its flat Qt
library layout. Modified plugins and the outer app bundle are re-signed, then
the complete bundle signature is verified before the build is accepted.

## Bundled and writable data

Bundled read-only resources:

- `config.yaml`
- `src/gui/resources/QR_UPI.jpeg`
- `src/gui/resources/icon.png`
- `src/gui/resources/icon.ico`
- `src/gui/resources/icon.icns`

`version.py` is compiled as a Python module and is not duplicated as editable
data. `Market Holidays` is no longer bundled because calendars are retrieved
from the official NSE endpoint and cached under the user profile.

Writable runtime state remains outside the application bundle:

- Market data: `~/NSE_BSE_Data/`
- Preferences, update cache and holiday cache: `~/.nse_bse_downloader/`

All default resource paths are resolved relative to the source/bundle root,
not the process working directory.

## macOS identity and later release gates

- Product: `NSE BSE Data Downloader`
- Bundle ID: `com.github.pparesh25.NSEBSEDownloader`
- GUI app mode with multiple instances prohibited
- Compilation report target: `dist/nuitka-compilation-report.xml`

Qt application metadata, the main-window title and the source-mode process
title all use the same product identity. `setproctitle` makes interpreted runs
readable in macOS process viewers; a future Nuitka `.app` will provide the
native executable/bundle identity.

Before a distributable release, the remaining gates are:

1. Configure the protected `release-signing` GitHub environment and credentials.
2. Run the manual trusted workflow for the exact intended commit.
3. Inspect signature, notarization, compilation, smoke-test, and checksum
   evidence.
4. Complete interactive first-launch and segment checks on fresh macOS and
   Windows machines using an isolated data root.
5. Publish immutable release assets and place the reviewed URL/SHA-256 pair in
   `version.py` only after acceptance.
