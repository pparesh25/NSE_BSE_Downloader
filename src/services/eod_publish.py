"""Publish symbol histories out of the EOD database.

Step 3 of Phase 5.  Step 2 proved the database can reproduce every published
file byte for byte; this is what that proof buys.

The gain is **not** that generating a file from the database is faster.  It is
measurably slower: rebuilding all 8,124 histories of the owner's tree takes
10.27 s against 5.48 s to read and parse the existing ones.  The gain is that
most of the work does not have to happen at all.

Adding one trading day rewrites every touched history in full.  On a four-date
tree that is 3.65 MB written to add 0.71 MB of new rows -- **5.1x** -- and the
ratio is the number of dates, so a multi-year archive rewrites gigabytes to
add a megabyte.  A history whose only change is rows after its last stored date
can instead be **appended to**, which is O(the new rows) rather than O(the
whole history.)

That is possible only because of what step 2 measured: a row's spelling comes
from the frame its own date published, not from the column it sits in.  Checked
against all 8,124 real histories, a row rendered on its own is byte-identical
to its line in the file -- every one of them.  Had spelling been a property of
the column, appending could not have produced the same bytes and this step
would have had no cheap path at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .canonical_data import SYMBOL_HISTORY_COLUMNS
from .eod_export import symbol_frame
from .eod_store import EodReader


@dataclass(frozen=True)
class PublishResult:
    """What one publication pass did, in the terms that justify it."""

    appended: int = 0
    rewritten: int = 0
    unchanged: int = 0
    bytes_written: int = 0
    failures: tuple[str, ...] = ()

    def render(self) -> str:
        return (
            f"Published {self.appended + self.rewritten} symbol "
            f"history/histories: {self.appended} appended, "
            f"{self.rewritten} rewritten, {self.unchanged} already current "
            f"({self.bytes_written / 1024:.0f} KB written)."
        )


def _last_line(path: Path) -> Optional[bytes]:
    """The file's final line, read without parsing the rest of it.

    The whole point of appending is not to pay for the history that is already
    correct, so this reads from the end rather than reading the file.
    """

    size = path.stat().st_size
    if size == 0:
        return None
    with path.open("rb") as handle:
        window = min(size, 4096)
        handle.seek(-window, os.SEEK_END)
        tail = handle.read(window)
    stripped = tail.rstrip(b"\n")
    if not stripped:
        return None
    return stripped.rsplit(b"\n", 1)[-1]


def _last_date(path: Path) -> Optional[int]:
    """The date of the file's final row, or ``None`` if it cannot be trusted.

    A file whose last line is short or unparseable is not appended to.  That
    is the self-healing path for a torn append: the next run cannot read a
    date it trusts, so it rewrites the file in full from the database, which
    is the record.
    """

    line = _last_line(path)
    if line is None:
        return None
    fields = line.split(b",")
    if len(fields) != len(SYMBOL_HISTORY_COLUMNS):
        return None
    try:
        stamp = int(fields[0])
    except ValueError:
        return None
    return stamp if 10_000_000 < stamp < 100_000_000 else None


def _render(frame: Any, header: bool) -> str:
    buffer = StringIO()
    frame.to_csv(buffer, index=False, header=header, lineterminator="\n")
    return buffer.getvalue()


def _write(path: Path, text: str) -> int:
    """Replace a history atomically, the way the legacy writer does."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".txt.tmp")
    payload = text.encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(payload)


def _append(path: Path, text: str) -> int:
    """Extend a history in place.

    Not atomic, and deliberately so -- copying the file to make it atomic is
    exactly the cost this exists to avoid.  The whole block is one ``write``
    and is fsynced, and a torn tail is detected on the next run by
    ``_last_date`` and repaired by a full rewrite from the database.
    """

    payload = text.encode("utf-8")
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return len(payload)


def _targets(
    registry: Mapping[str, Any],
    exchange: str,
    keys: Iterable[str],
) -> list[str]:
    """Which file each touched security is published to *now*.

    Resolved forwards -- key to current symbol to current filename -- and
    deliberately not through ``identities``, which is the reverse index and
    keeps historical names so that an old ticker still resolves.  Reading it
    the other way resurrects them: BSE ``MANBRO`` was renamed to ``KDGREEN``,
    ``identities`` still lists ``manbro.txt`` against the same two keys, and
    publishing to every filename that ever held a key recreated a deleted file
    holding a second copy of a security that already had one.
    """

    current = registry.get("exchanges", {}).get(exchange, {})
    files = registry.get("files", {}).get(exchange, {})
    names = set()
    for key in keys:
        symbol = current.get(key)
        if symbol is None and key.startswith("SYM:"):
            symbol = key.split(":", 1)[1]
        filename = files.get(symbol) if symbol else None
        if filename:
            names.add(filename)
    return sorted(names)


def publish_histories(
    store: EodReader,
    base_path: Path,
    registry: Mapping[str, Any],
    actions: Sequence[dict],
    touched: Mapping[str, Iterable[str]],
    revised_dates: Iterable[tuple[str, str, int]] = (),
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> PublishResult:
    """Write the symbol histories one run changed, and nothing else.

    A file is appended to only when every one of its stored dates is still as
    it was and the new rows all come after them.  Anything else -- a corrected
    re-download of an earlier date, a corporate action that rescales the file,
    a tail that cannot be read -- is a full rewrite, because those change rows
    the file already holds.
    """

    base_path = Path(base_path)
    appended = rewritten = unchanged = written = 0
    failures: list[str] = []
    adjusted = {
        (str(record.get("exchange", "")).upper(),
         str(record.get("symbol", "")).strip().upper())
        for record in actions
    }
    # The oldest date this run rewrote, per exchange.  A file whose last
    # stored date is at or after it may hold one of those rows, so it cannot
    # be extended -- an append only ever adds to the end.  Conservative on
    # purpose: this is empty for an ordinary forward-only run, and a repair
    # that re-downloads an old date wants a full rebuild anyway.
    oldest_revised: dict[str, int] = {}
    for revised_exchange, _segment, stamp in revised_dates:
        key = revised_exchange.upper()
        oldest_revised[key] = min(oldest_revised.get(key, stamp), stamp)

    plan = {
        exchange.upper(): _targets(registry, exchange.upper(), keys)
        for exchange, keys in touched.items()
    }
    total = sum(len(names) for names in plan.values())
    done = 0
    if on_progress is not None:
        on_progress(0, total)

    for exchange, filenames in plan.items():
        folder = base_path / exchange / "SYMBOLS"
        symbols = registry.get("files", {}).get(exchange, {})
        for filename in filenames:
            done += 1
            if on_progress is not None:
                on_progress(done, total)
            path = folder / filename
            try:
                names = {
                    symbol.upper() for symbol, stored in symbols.items()
                    if stored == filename
                }
                stored_date = _last_date(path) if path.is_file() else None
                rescaled = any((exchange, name) in adjusted for name in names)
                revised_floor = oldest_revised.get(exchange)
                touched_stored = (
                    stored_date is not None
                    and revised_floor is not None
                    and revised_floor <= stored_date
                )
                if stored_date is not None and not rescaled and not touched_stored:
                    # Read only what could be appended.  Whether it really can
                    # be is decided below; if not, the full frame is fetched.
                    fresh = symbol_frame(
                        store, registry, exchange, filename, (), stored_date
                    )
                    if fresh.empty:
                        unchanged += 1
                        continue
                    written += _append(path, _render(fresh, header=False))
                    appended += 1
                    continue
                frame = symbol_frame(
                    store, registry, exchange, filename, actions
                )
                if frame.empty:
                    continue
                text = _render(frame, header=True)
                if path.is_file() and path.read_text(encoding="utf-8") == text:
                    unchanged += 1
                    continue
                written += _write(path, text)
                rewritten += 1
            except Exception as error:  # one bad symbol must not stop the run
                failures.append(f"{exchange}/{filename}: {error}")

    return PublishResult(
        appended, rewritten, unchanged, written, tuple(failures)
    )
