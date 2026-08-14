# Upgrading from v1.0.1 to v1.1.0 — read this first

**If you arrived here from the "New version available" popup inside the app, this file
is the one you need.** The download you just received is application source code, not a
ready-to-run installer, and v1.1.0 needs a different set of libraries than v1.0.1 did.

There were two ways forward. **Option A is unavailable right now**, so Option B is the
one to follow.

---

## Option A — Download a prebuilt application (withdrawn)

**The prebuilt v1.1.0 applications have been withdrawn, and no release is published at
the moment.** The releases page is empty on purpose; there is nothing there to
download.

They were withdrawn because they could not download market data at all. The packaged
application carried its own copy of OpenSSL but no certificate store, so every HTTPS
request to NSE and BSE failed certificate verification, and the app reported an "SSL
certificate issue" for every date. The exchanges were fine; the build was not.

**Running from source is not affected by this**, because it uses the certificates your
own Python installation already trusts. Use Option B. This section will return, with
the file names and first-launch instructions, once a corrected build is published.

---

## Option B — Run from source

You need **Python 3.10 or newer**. Check with:

```bash
python3 --version
```

If that shows 3.9 or older, install a newer Python from https://www.python.org/downloads/
before continuing.

Then, from inside the folder you downloaded:

```bash
pip install -r requirements.txt
python main.py
```

**This step is not optional.** v1.0.1 used a GUI library called PyQt6. v1.1.0 uses
PySide6. If you skip `pip install`, the app will not start.

---

## The most common upgrade problem

If you copied the new files over your old installation and now see this:

```
ModuleNotFoundError: No module named 'PySide6'
```

…the app is working correctly and simply telling you the new library is missing. Fix it
with:

```bash
pip install -r requirements.txt
```

Other errors and what they mean:

| Error | Cause | Fix |
|---|---|---|
| `No module named 'PySide6'` | Dependencies not installed | `pip install -r requirements.txt` |
| `SyntaxError` on startup | Python too old | Install Python 3.10+ |
| `ERROR: Could not find a version that satisfies the requirement PySide6` | Your OS is older than PySide6 supports (needs macOS 13+, or a recent Linux) | Use an older OS-compatible release, or see "Older systems" below |

### Older systems

PySide6 requires macOS 13 (Ventura) or newer, and a reasonably recent Linux. If `pip`
refuses to install it, your system is below that floor. The v1.0.1 source remains
available and functional, under "Source code" on its tag page:

**https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.0.1**

---

## What happens to my existing downloaded data?

**Nothing is deleted, moved, or rewritten.** Your `~/NSE_BSE_Data/` folder is safe.

There are three things worth knowing.

**1. New files have more columns than old ones.**

v1.0.1 wrote 7 columns per row. v1.1.0 writes 9 for equity, SME and futures files:

```
Equity / SME : SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,DELIVERY_QTY,DELIVERY_PERCENT
Futures      : SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,OPEN_INTEREST,CHANGE_IN_OI
Index        : SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME          (unchanged, 7 columns)
```

Files you already have keep their old 7-column shape until you download that date again.
So your folder will contain a mix of both formats.

If you have your own scripts reading these files, either handle both widths, or
re-download the dates you care about to convert them.

**2. Symbol-wise history files start empty.**

`NSE/SYMBOLS/reliance.txt` and similar per-symbol files are new in v1.1.0. They are
built from downloads made *after* you upgrade — existing daily files are not converted
into them automatically. To populate history for past dates, re-download that date range.

**3. Your settings carry over, mostly.**

`~/.nse_bse_downloader/user_preferences.json` is read and validated on first launch.
Recognised settings are kept. A few v1.0.1 settings that no longer control anything are
dropped. Nothing crashes; you may just want to check the Settings panel once.

---

## Before your first big download

v1.1.0 turns on four extra per-date steps by default: delivery data, futures open
interest, symbol histories, and corporate-action adjustment. Downloads are therefore
slower per date than v1.0.1 was.

**If you enable an exchange segment you have never downloaded before**, the app starts
from its configured base date and may queue many months of work in one run. Watch the
date range shown before clicking Start, and narrow it if it is larger than you expected.

---

## Going back to v1.0.1

v1.0.1 still runs and still reads your data folder. Download its source from the
[v1.0.1 tag](https://github.com/pparesh25/NSE_BSE_Downloader/releases/tag/v1.0.1).

Be aware this is not a clean round trip: 9-column files written by v1.1.0 stay
9-column, and v1.0.1 does not understand the `.state` folder v1.1.0 creates. It will
ignore both rather than break, but your folder will not return to a pure v1.0.1 state.

---

## Still stuck?

Open an issue with your operating system, Python version (`python3 --version`), and the
exact error text:

**https://github.com/pparesh25/NSE_BSE_Downloader/issues**
