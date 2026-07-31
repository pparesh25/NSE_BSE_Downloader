# Nuitka packaging preparation

## Current status

The repository is prepared for a Nuitka packaged-app build, but no binary or
app bundle has been built yet. The build entry point defaults to a no-write dry
run and requires `--build` before it can start compilation.

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

## Bundled and writable data

Bundled read-only resources:

- `config.yaml`
- `src/gui/resources/QR_UPI.jpeg`

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

Before a distributable release, the remaining gates are:

1. Add a project-owned `.icns` icon (the command safely uses Nuitka's default
   icon until one exists).
2. Run the actual Apple Silicon build and inspect its compilation report.
3. Configure Developer ID signing and notarization credentials outside Git.
4. Test first launch from a fresh macOS user account, data-folder permissions,
   QR rendering, update notification and every segment download.
5. Publish an immutable release asset and place its URL/SHA-256 in `version.py`.
