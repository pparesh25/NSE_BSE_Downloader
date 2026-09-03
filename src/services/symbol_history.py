"""Per-symbol text histories backed by canonical internal daily snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
import shutil
from threading import Lock
from typing import Any, Callable, Iterable, List, Optional, Sequence

import pandas as pd

from .canonical_data import (
    EXTENDED_MARKET_COLUMNS,
    INTERNAL_EQUITY_COLUMNS,
    PRE_EXTENDED_INTERNAL_EQUITY_COLUMNS,
    PRE_EXTENDED_SYMBOL_HISTORY_COLUMNS,
    ISIN_PATTERN,
    LEGACY_SYMBOL_HISTORY_COLUMNS,
    SYMBOL_HISTORY_COLUMNS,
    valid_isin,
    valid_security_id,
)
from .state_store import (
    StateCorruptionError,
    StateStoreError,
    VersionedJSONStore,
    default_corporate_ledger,
    file_sha256,
    migrate_corporate_ledger,
    quarantine_copy,
    quarantine_move,
    validate_corporate_ledger,
)


# Per-symbol failures are isolated so one unreadable history cannot hold back
# a whole backfill.  Past this many, the store itself is the problem -- a full
# disk fails every symbol -- and failing fast beats writing thousands of
# identical error records.
MAX_ISOLATED_SYMBOL_FAILURES = 100


class HistoryCorruptionError(StateStoreError):
    """Raised when a symbol history is unsafe to update in place."""

    def __init__(
        self, path: Path, quarantine_path: Optional[Path], reason: Exception
    ):
        self.path = Path(path)
        self.quarantine_path = quarantine_path
        self.reason = reason
        message = f"Symbol history is corrupt and was not modified: {path}"
        if quarantine_path is not None:
            message += f" (backup: {quarantine_path})"
        message += f": {reason}"
        super().__init__(message)


@dataclass(frozen=True)
class HistoryBatchItem:
    """One validated daily frame queued for a history batch."""

    exchange: str
    segment: str
    target_date: date
    rows: pd.DataFrame


@dataclass(frozen=True)
class HistorySymbolFailure:
    """One symbol file that could not be published, and what it holds back.

    ``entries`` names every ``(exchange, segment, date)`` whose rows were in
    that file, so the caller can fail exactly those dates for repair and
    still publish the rest of the batch.
    """

    path: str
    error: str
    entries: tuple[tuple[str, str, date], ...]


@dataclass(frozen=True)
class MergeRefusal:
    """A rename-driven merge that was refused because histories overlap.

    A genuine rename retires one ticker before the next one trades, so two
    files claiming the same dates are two different securities -- merging
    them would let deduplication destroy one row per shared date.  Both
    files are left untouched and the refusal is surfaced to the caller.
    """

    exchange: str
    old_symbol: str
    new_symbol: str
    old_path: str
    new_path: str
    overlapping_dates: int
    sample_dates: tuple[str, ...]


@dataclass(frozen=True)
class HistoryBatchResult:
    """Observable I/O outcome for one symbol-history batch."""

    entries: int
    rows: int
    symbols: int
    history_reads: int
    history_writes: int
    failures: tuple[HistorySymbolFailure, ...] = ()
    refused_merges: tuple[MergeRefusal, ...] = ()


@dataclass(frozen=True)
class _ActionIndex:
    """Applied corporate actions, indexed so most symbols skip the replay.

    Replaying actions is the most expensive step in a batch and applies to
    almost no symbols, so the membership sets exist purely to answer "can any
    audited action reach this symbol?" before any per-row work starts.
    """

    records: tuple[dict, ...]
    symbols: frozenset[tuple[str, str]]
    identifiers: frozenset[tuple[str, str]]

    @classmethod
    def build(cls, records: List[dict]) -> "_ActionIndex":
        symbols: set[tuple[str, str]] = set()
        identifiers: set[tuple[str, str]] = set()
        for record in records:
            exchange = str(record.get("exchange", "")).strip().upper()
            symbol = str(record.get("symbol", "")).strip().upper()
            if symbol:
                symbols.add((exchange, symbol))
            stable_id = str(record.get("stable_id", "")).strip().upper()
            if stable_id:
                identifiers.add((exchange, stable_id))
        return cls(tuple(records), frozenset(symbols), frozenset(identifiers))

    def touches(
        self,
        exchange: str,
        symbols: Iterable[str],
        identifiers: Iterable[str],
    ) -> bool:
        """Mirror the two match rules in ``_apply_recorded_actions``."""

        if not self.records:
            return False
        if any((exchange, symbol) in self.symbols for symbol in symbols):
            return True
        return any(
            (exchange, identifier) in self.identifiers
            for identifier in identifiers
        )


@dataclass(frozen=True)
class _BatchPlan:
    """Where every batched row is published, resolved before any file I/O.

    ``contributions`` maps a final symbol file to the ``(batch index, row
    position, symbol)`` triples that belong in it, ``merge_sources`` records
    the files a rename must fold in first, and ``retired`` holds the files a
    rename leaves behind.
    """

    contributions: dict[Path, list[tuple[int, int, str]]]
    merge_sources: dict[Path, list[Path]]
    retired: set[Path]
    rows_seen: int
    refusals: tuple[MergeRefusal, ...] = ()


class SymbolHistoryStore:
    """Maintain deterministic NSE/BSE ``symbol.txt`` history files."""

    _lock = Lock()

    def __init__(self, base_data_path: Path):
        self.base_path = Path(base_data_path)
        self.state_path = self.base_path / ".state"
        self.registry_path = self.state_path / "symbol_registry.json"
        self.raw_path = self.state_path / "raw"
        self._registry_state = VersionedJSONStore(
            self.registry_path,
            default=self._empty_registry(),
            validator=self._validate_registry,
            quarantine_root=self.state_path / "quarantine",
            category="symbol_registry",
            migrate=self._migrate_registry,
        )

    @staticmethod
    def _empty_registry() -> dict[str, Any]:
        return {"version": 3, "exchanges": {}, "files": {}, "identities": {}}

    @staticmethod
    def _valid_stable_key(key: Any) -> bool:
        if not isinstance(key, str) or ":" not in key:
            return False
        kind, value = key.split(":", 1)
        if kind == "ISIN":
            return valid_isin(value)
        if kind == "ID":
            return valid_security_id(value)
        return False

    @classmethod
    def _migrate_registry(cls, data: dict[str, Any]) -> dict[str, Any]:
        if "version" not in data:
            is_legacy = all(
                isinstance(exchange, str) and isinstance(values, dict)
                for exchange, values in data.items()
            )
            if is_legacy:
                data = {
                    "version": 2,
                    "exchanges": data,
                    "files": {},
                }
        if data.get("version") == 2:
            # v3 records which identity owns each filename.  Invert the
            # existing key->symbol mappings through the filename mappings so
            # every previously written file keeps its owner, and drop keys a
            # malformed identifier smuggled in before keys were validated --
            # such a key is shared by unrelated symbols and must not merge
            # them, which is the very defect identity keying exists to stop.
            exchanges = data.get("exchanges")
            files = data.get("files")
            if isinstance(exchanges, dict) and isinstance(files, dict):
                identities: dict[str, dict[str, list[str]]] = {}
                for exchange, mappings in exchanges.items():
                    if not isinstance(mappings, dict):
                        continue
                    for key in [key for key in mappings if not cls._valid_stable_key(key)]:
                        del mappings[key]
                    file_mappings = files.get(exchange)
                    if not isinstance(file_mappings, dict):
                        continue
                    owners = identities.setdefault(exchange, {})
                    for key, symbol in mappings.items():
                        filename = file_mappings.get(symbol)
                        if isinstance(filename, str):
                            owned = owners.setdefault(filename, [])
                            if key not in owned:
                                owned.append(key)
                for owners in identities.values():
                    for owned in owners.values():
                        owned.sort()
                data = {
                    "version": 3,
                    "exchanges": exchanges,
                    "files": files,
                    "identities": identities,
                }
        return data

    @classmethod
    def _validate_registry(cls, data: dict[str, Any]) -> None:
        if data.get("version") != 3:
            raise ValueError("unsupported symbol-registry version")
        exchanges = data.get("exchanges")
        files = data.get("files")
        identities = data.get("identities")
        if (
            not isinstance(exchanges, dict)
            or not isinstance(files, dict)
            or not isinstance(identities, dict)
        ):
            raise ValueError("symbol registry sections must be objects")
        for exchange, mappings in exchanges.items():
            if not isinstance(exchange, str) or not isinstance(mappings, dict):
                raise ValueError("invalid symbol registry exchange")
            if not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in mappings.items()
            ):
                raise ValueError("invalid stable-identifier mapping")
        for exchange, mappings in files.items():
            if not isinstance(exchange, str) or not isinstance(mappings, dict):
                raise ValueError("invalid symbol filename exchange")
            filenames = list(mappings.values())
            if not all(
                isinstance(symbol, str)
                and isinstance(filename, str)
                and filename.endswith(".txt")
                and Path(filename).name == filename
                for symbol, filename in mappings.items()
            ):
                raise ValueError("invalid symbol filename mapping")
            if len(filenames) != len(set(filenames)):
                raise ValueError("symbol filename mappings must be unique")
        for exchange, owners in identities.items():
            if not isinstance(exchange, str) or not isinstance(owners, dict):
                raise ValueError("invalid filename identity exchange")
            for filename, keys in owners.items():
                if (
                    not isinstance(filename, str)
                    or not isinstance(keys, list)
                    or not all(isinstance(key, str) for key in keys)
                ):
                    raise ValueError("invalid filename identity mapping")

    @staticmethod
    def safe_filename(symbol: str) -> str:
        safe = re.sub(r"[^a-z0-9._-]+", "_", str(symbol).strip().lower())
        safe = safe.strip("._-")
        return safe or "unknown_symbol"

    def symbol_path(self, exchange: str, symbol: str) -> Path:
        exchange = exchange.upper()
        symbol = str(symbol).strip().upper()
        registry = self._read_registry()
        filename = registry["files"].get(exchange, {}).get(
            symbol, f"{self.safe_filename(symbol)}.txt"
        )
        return self.base_path / exchange / "SYMBOLS" / filename

    def _path_from_registry(
        self, registry: dict, exchange: str, symbol: str
    ) -> Path:
        exchange = exchange.upper()
        symbol = str(symbol).strip().upper()
        filename = registry["files"].get(exchange, {}).get(
            symbol, f"{self.safe_filename(symbol)}.txt"
        )
        return self.base_path / exchange / "SYMBOLS" / filename

    def resolve_symbol(self, exchange: str, stable_id: str) -> Optional[str]:
        """Resolve the latest ticker by ISIN/security code."""

        exchange_registry = self._read_registry()["exchanges"].get(
            exchange.upper(), {}
        )
        stable_id = str(stable_id).strip().upper()
        return (
            exchange_registry.get(f"ISIN:{stable_id}")
            or exchange_registry.get(f"ID:{stable_id}")
        )

    def _read_registry(self) -> dict[str, Any]:
        return self._registry_state.read()

    def _write_registry(self, registry: dict[str, Any]) -> None:
        self._registry_state.write(registry)

    def _ensure_symbol_filename(
        self,
        registry: dict[str, Any],
        exchange: str,
        symbol: str,
        stable_keys: List[str],
    ) -> str:
        """Resolve the file a symbol's rows belong in, keyed on identity.

        A ticker is not an identity: exchanges reassign a delisted ticker to a
        new company, and before identity keying the new company's rows were
        appended into the delisted company's file, where deduplication then
        destroyed one row per shared date.  Each filename therefore remembers
        the stable keys of the security it was created for, and a security
        with disjoint keys is given a fresh digest-suffixed file instead.
        The entry outlives the file itself, so a name retired by a rename in
        the same batch cannot be handed to a different security either.
        """

        exchange = exchange.upper()
        symbol = str(symbol).strip().upper()
        mappings = registry["files"].setdefault(exchange, {})
        owners = registry["identities"].setdefault(exchange, {})
        keys = set(stable_keys)

        def claims(filename: str) -> bool:
            owned = set(owners.get(filename, ()))
            return not owned or not keys or bool(owned & keys)

        def claim(filename: str) -> str:
            if keys:
                owned = owners.get(filename)
                if owned is None or not keys.issubset(owned):
                    owners[filename] = sorted(set(owned or ()) | keys)
            mappings[symbol] = filename
            return filename

        existing = mappings.get(symbol)
        if existing and claims(existing):
            return claim(existing)

        base = self.safe_filename(symbol)
        candidate = f"{base}.txt"
        used = {filename: owner for owner, filename in mappings.items()}
        identity = stable_keys[0] if stable_keys else symbol
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        width = 8
        while (
            candidate in used and used[candidate] != symbol
        ) or not claims(candidate):
            candidate = f"{base}--{digest[:width]}.txt"
            width += 2
        return claim(candidate)

    @staticmethod
    def _history_rows(rows: pd.DataFrame) -> pd.DataFrame:
        frame = rows.copy()
        for column in SYMBOL_HISTORY_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        frame["DATE"] = pd.to_datetime(
            frame["DATE"].astype(str), format="%Y%m%d", errors="coerce"
        ).dt.strftime("%Y%m%d")
        # The published ISIN column asserts identity, so only a value with
        # the identifier's shape is written; anything else becomes blank.
        isin = frame["ISIN"].fillna("").astype(str).str.strip().str.upper()
        frame["ISIN"] = isin.where(isin.str.fullmatch(ISIN_PATTERN), "")
        return frame.loc[:, SYMBOL_HISTORY_COLUMNS]

    @staticmethod
    def _upgrade_legacy_history(frame: pd.DataFrame) -> pd.DataFrame:
        """Bring an older history up to the current schema, in order.

        Two generations precede the current one: the pre-ISIN eleven-column
        file, and the twelve-column file written before turnover and previous
        close existed.  Both upgrade by adding empty columns, because an empty
        field says "this file was written before the exchange value was
        collected" while a zero would claim the session had no turnover.
        """

        columns = list(frame.columns)
        if columns == LEGACY_SYMBOL_HISTORY_COLUMNS:
            frame = frame.copy()
            frame["ISIN"] = ""
            columns = list(frame.columns)
        if columns == PRE_EXTENDED_SYMBOL_HISTORY_COLUMNS:
            frame = frame.copy()
            for column in EXTENDED_MARKET_COLUMNS:
                frame[column] = ""
        return frame

    @staticmethod
    def _deduplicate(frame: pd.DataFrame) -> pd.DataFrame:
        """Keep one row per date: the newest, which is the last one offered.

        Every caller concatenates the incoming rows after the stored ones, so
        "last" means "recomputed from the current raw data by the current
        rules".  This used to rank by VOLUME instead, which discarded an
        exchange's corrected bhavcopy whenever the correction reduced volume --
        and once a corporate action began scaling volume, it let a stale row
        outrank its own repair, silently and with no failure recorded.
        """

        if frame.empty:
            return frame
        result = (
            frame.sort_values("DATE", kind="stable")
            .drop_duplicates("DATE", keep="last")
        )
        return result.loc[:, SYMBOL_HISTORY_COLUMNS]

    def _read_history(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=SYMBOL_HISTORY_COLUMNS)
        try:
            frame = self._upgrade_legacy_history(pd.read_csv(path, dtype=str))
            self._validate_history(frame)
            return frame.loc[:, SYMBOL_HISTORY_COLUMNS]
        except HistoryCorruptionError:
            raise
        except Exception as error:
            quarantine_path = quarantine_copy(
                path, self.state_path / "quarantine", "history"
            )
            raise HistoryCorruptionError(
                path, quarantine_path, error
            ) from error

    @staticmethod
    def _validate_history(frame: pd.DataFrame) -> None:
        if list(frame.columns) != SYMBOL_HISTORY_COLUMNS:
            raise ValueError(
                "history columns do not match the required schema"
            )
        if frame.empty:
            return
        dates = frame["DATE"].astype(str)
        if not dates.str.fullmatch(r"\d{8}").all():
            raise ValueError("history contains an invalid date value")
        parsed_dates = pd.to_datetime(
            dates, format="%Y%m%d", errors="coerce"
        )
        if parsed_dates.isna().any() or dates.duplicated().any():
            raise ValueError("history contains invalid or duplicate dates")
        for column in ("OPEN", "HIGH", "LOW", "CLOSE"):
            if pd.to_numeric(frame[column], errors="coerce").isna().any():
                raise ValueError(f"history contains invalid {column} values")
        isin = frame["ISIN"].fillna("").astype(str)
        if not (isin.eq("") | isin.str.fullmatch(ISIN_PATTERN)).all():
            raise ValueError("history contains an invalid ISIN value")

    def _write_history(self, path: Path, frame: pd.DataFrame) -> None:
        # Deliberately no ``.state/backups/history`` copy.  Until v1.1.0 every
        # symbol write also copied the file it was about to replace, which
        # doubled this stage's write volume for a tree nothing ever read.  It
        # was not a restore point either: a batch rewrites each touched symbol
        # once per bucket, so the copy held whatever the previous bucket wrote,
        # not the state before the run.  The recoverable source of truth is
        # ``.state/raw`` -- checksummed, per date, and what ``--rebuild-*``
        # actually reads.
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".txt.tmp")
        normalized = self._deduplicate(frame)
        self._validate_history(normalized)
        try:
            normalized.to_csv(temporary, index=False, lineterminator="\n")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_new_single_history(
        self, path: Path, frame: pd.DataFrame
    ) -> None:
        """Publish a validated one-row batch without generic sort/dedup work."""

        if path.exists() or len(frame) != 1:
            raise ValueError("single-history fast path requires one new row")
        if list(frame.columns) != SYMBOL_HISTORY_COLUMNS:
            raise ValueError(
                "history columns do not match the required schema"
            )
        row = frame.iloc[0]
        date_value = str(row["DATE"])
        if not re.fullmatch(r"\d{8}", date_value):
            raise ValueError("history contains an invalid date value")
        try:
            datetime.strptime(date_value, "%Y%m%d")
        except ValueError as error:
            raise ValueError("history contains an invalid date value") from error
        for column in ("OPEN", "HIGH", "LOW", "CLOSE"):
            if pd.isna(pd.to_numeric(row[column], errors="coerce")):
                raise ValueError(f"history contains invalid {column} values")
        isin_text = "" if pd.isna(row["ISIN"]) else str(row["ISIN"]).strip()
        if isin_text and not valid_isin(isin_text):
            raise ValueError("history contains an invalid ISIN value")

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".txt.tmp")
        try:
            frame.to_csv(temporary, index=False, lineterminator="\n")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _history_dates(self, path: Path) -> Optional[set[str]]:
        """Return the dates stored in one history, or ``None`` if unreadable.

        ``None`` means "cannot check": the caller proceeds and lets the full
        read raise its ordinary corruption error, which quarantines the bytes.
        """

        if not path.exists():
            return set()
        try:
            frame = pd.read_csv(path, usecols=["DATE"], dtype=str)
            return set(frame["DATE"].astype(str))
        except Exception:
            return None

    def _refusal(
        self,
        exchange: str,
        old_symbol: str,
        new_symbol: str,
        old_path: Path,
        new_path: Path,
        overlap: set[str],
    ) -> MergeRefusal:
        def relative(path: Path) -> str:
            try:
                return str(path.relative_to(self.base_path))
            except ValueError:
                return str(path)

        return MergeRefusal(
            exchange=exchange.upper(),
            old_symbol=old_symbol.upper(),
            new_symbol=new_symbol.upper(),
            old_path=relative(old_path),
            new_path=relative(new_path),
            overlapping_dates=len(overlap),
            sample_dates=tuple(sorted(overlap)[:3]),
        )

    def _quarantine_superseded(self, path: Path) -> None:
        """Retire a merged-away file into quarantine rather than deleting it.

        After a successful merge its rows live on in the merged file, so the
        copy is a recovery point for a wrong merge, not the data itself.
        """

        quarantine_move(path, self.state_path / "quarantine", "merged")

    def _merge_renamed_file(
        self,
        registry: dict[str, Any],
        exchange: str,
        old_symbol: str,
        new_symbol: str,
    ) -> Optional[MergeRefusal]:
        old_path = self._path_from_registry(registry, exchange, old_symbol)
        new_path = self._path_from_registry(registry, exchange, new_symbol)
        if old_path == new_path or not old_path.exists():
            return None
        old_dates = self._history_dates(old_path)
        new_dates = self._history_dates(new_path)
        if old_dates and new_dates:
            overlap = old_dates & new_dates
            if overlap:
                # Two files claiming the same dates are two securities, not a
                # rename.  Keep both files and both registry mappings.
                return self._refusal(
                    exchange, old_symbol, new_symbol, old_path, new_path,
                    overlap,
                )
        combined = self._read_history(old_path)
        if new_path.exists():
            combined = pd.concat(
                [combined, self._read_history(new_path)], ignore_index=True
            )
        self._write_history(new_path, combined)
        self._quarantine_superseded(old_path)
        registry["files"].setdefault(exchange.upper(), {}).pop(
            old_symbol.upper(), None
        )
        return None

    @staticmethod
    def _identifier(value: Any) -> str:
        """Normalize one raw identifier, rejecting pandas' null spellings."""

        text = str(value).strip().upper()
        return "" if text in {"", "NAN", "<NA>"} else text

    @classmethod
    def _stable_keys_from(cls, isin: Any, security_id: Any) -> List[str]:
        """Return every stable identifier available for the security.

        BSE corporate actions are keyed by security code while its price files
        also contain an ISIN.  Registering both avoids losing rename/action
        resolution by always preferring only one identifier.

        A value that does not have the identifier's shape is dropped rather
        than registered: stable keys are merge keys, and one malformed value
        repeated across unrelated rows would register them all as the same
        security and fold their histories together.
        """

        keys = []
        value = cls._identifier(isin)
        if value and valid_isin(value):
            keys.append(f"ISIN:{value}")
        value = cls._identifier(security_id)
        if value and valid_security_id(value):
            keys.append(f"ID:{value}")
        return keys

    @classmethod
    def _stable_keys(cls, row: pd.Series) -> List[str]:
        return cls._stable_keys_from(
            row.get("ISIN", ""), row.get("SECURITY_ID", "")
        )

    def _read_applied_actions(self) -> List[dict]:
        ledger_path = self.state_path / "corporate_actions.json"
        ledger = VersionedJSONStore(
            ledger_path,
            default=default_corporate_ledger(),
            validator=validate_corporate_ledger,
            quarantine_root=self.state_path / "quarantine",
            category="corporate_actions",
            migrate=migrate_corporate_ledger,
        ).read()
        return [
            record for record in ledger["actions"].values()
            if record.get("status") == "applied"
        ]

    def _apply_recorded_actions(
        self,
        exchange: str,
        symbols: str | Iterable[str],
        row: pd.Series,
        action_records: Sequence[dict],
    ) -> pd.Series:
        """Reapply audited actions when a historical raw row is re-downloaded.

        Without this step, retrying delivery or repairing an old daily report
        could replace an already adjusted symbol-history row with raw OHLC.
        Only actions recorded as successfully applied are considered.

        ``symbols`` names every ticker the security is known by, not only the
        one on the row.  An action whose ``stable_id`` fell back to a ticker
        matches by name alone, and after a rename that name is not the one on
        a re-downloaded historical row -- the engine applied it to the whole
        merged file, so the replay must recognize both names or it silently
        reverts the adjustment.
        """

        if not action_records:
            return row

        known = (
            {symbols.strip().upper()}
            if isinstance(symbols, str)
            else {str(value).strip().upper() for value in symbols}
        )
        identifiers = {
            key.split(":", 1)[1] for key in self._stable_keys(row)
        }
        try:
            row_date = pd.to_datetime(
                str(row.get("DATE", "")), format="%Y%m%d", errors="raise"
            ).date()
        except Exception:
            return row

        applicable: dict[date, float] = {}
        for record in action_records:
            if str(record.get("exchange", "")).upper() != exchange.upper():
                continue
            stable_id = str(record.get("stable_id", "")).strip().upper()
            action_symbol = str(record.get("symbol", "")).strip().upper()
            if stable_id not in identifiers and action_symbol not in known:
                continue
            try:
                ex_date = date.fromisoformat(str(record["ex_date"]))
                action_factor = float(record["factor"])
            except (KeyError, TypeError, ValueError):
                continue
            if row_date < ex_date and action_factor > 0:
                applicable[ex_date] = applicable.get(ex_date, 1.0) * action_factor

        if not applicable:
            return row
        # Call the engine's own arithmetic rather than repeating it.  A replayed
        # row that disagreed with the row the engine wrote would be resolved by
        # _deduplicate on volume rank, silently, with no error anywhere.
        from .corporate_actions import adjust_rows

        frame = row.to_frame().T
        for ex_date in sorted(applicable):
            adjust_rows(frame, frame.index, applicable[ex_date])
        return frame.iloc[0]

    def save_internal_snapshot(
        self,
        exchange: str,
        segment: str,
        target_date,
        rows: pd.DataFrame,
    ) -> Path:
        """Save rebuildable canonical input outside the user-facing folders."""

        path = (
            self.raw_path / exchange.upper() / segment.upper() /
            f"{target_date.isoformat()}.csv"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".csv.tmp")
        metadata_path = path.with_suffix(".csv.meta.json")
        snapshot = rows.copy()
        for column in INTERNAL_EQUITY_COLUMNS:
            if column not in snapshot.columns:
                snapshot[column] = pd.NA
        snapshot = snapshot.loc[:, INTERNAL_EQUITY_COLUMNS]
        try:
            snapshot.to_csv(temporary, index=False, lineterminator="\n")
            new_digest = file_sha256(temporary)
            old_digest = file_sha256(path) if path.exists() else None

            if metadata_path.exists():
                def validate_existing_metadata(data: dict[str, Any]) -> None:
                    if data.get("version") != 1:
                        raise ValueError(
                            "unsupported raw-snapshot metadata version"
                        )
                    if old_digest is None or data.get("sha256") != old_digest:
                        raise ValueError(
                            "existing raw-snapshot checksum mismatch"
                        )
                    if not isinstance(data.get("row_count"), int):
                        raise ValueError(
                            "existing raw-snapshot row count is invalid"
                        )

                VersionedJSONStore(
                    metadata_path,
                    default={},
                    validator=validate_existing_metadata,
                    quarantine_root=self.state_path / "quarantine",
                    category="raw_snapshot_metadata",
                ).read()

            if old_digest is not None and old_digest != new_digest:
                revision = (
                    self.state_path / "raw_revisions" / exchange.upper() /
                    segment.upper() / target_date.isoformat() /
                    f"{old_digest}.csv"
                )
                revision.parent.mkdir(parents=True, exist_ok=True)
                if not revision.exists():
                    shutil.copy2(path, revision)
            temporary.replace(path)

            def validate_metadata(data: dict[str, Any]) -> None:
                if data.get("version") != 1:
                    raise ValueError(
                        "unsupported raw-snapshot metadata version"
                    )
                if data.get("sha256") != new_digest:
                    raise ValueError("raw-snapshot metadata checksum mismatch")
                if data.get("row_count") != len(snapshot):
                    raise ValueError(
                        "raw-snapshot metadata row count mismatch"
                    )

            VersionedJSONStore(
                metadata_path,
                default={},
                validator=validate_metadata,
                quarantine_root=self.state_path / "quarantine",
                category="raw_snapshot_metadata",
            ).write({
                "version": 1,
                "exchange": exchange.upper(),
                "segment": segment.upper(),
                "target_date": target_date.isoformat(),
                "row_count": len(snapshot),
                "sha256": new_digest,
                "written_at": pd.Timestamp.now(
                    tz="Asia/Kolkata"
                ).isoformat(),
            })
            return path
        finally:
            temporary.unlink(missing_ok=True)

    def read_internal_snapshot(self, path: Path) -> pd.DataFrame:
        """Read one checksummed canonical snapshot or fail closed."""

        path = Path(path)
        try:
            frame = pd.read_csv(path, dtype=str)
            columns = list(frame.columns)
            if columns == PRE_EXTENDED_INTERNAL_EQUITY_COLUMNS:
                # Written before turnover and previous close were collected.
                # Refusing it would send every snapshot already on disk to
                # quarantine the first time a rebuild ran, and the snapshots
                # are the only thing a rebuild has to work from.
                for column in EXTENDED_MARKET_COLUMNS:
                    frame[column] = pd.NA
                frame = frame.loc[:, INTERNAL_EQUITY_COLUMNS]
            elif columns != INTERNAL_EQUITY_COLUMNS:
                raise ValueError("raw snapshot schema mismatch")
            actual_digest = file_sha256(path)
            metadata_path = path.with_suffix(".csv.meta.json")

            def validate_metadata(data: dict[str, Any]) -> None:
                if data.get("version") != 1:
                    raise ValueError(
                        "unsupported raw-snapshot metadata version"
                    )
                if data.get("sha256") != actual_digest:
                    raise ValueError("raw snapshot checksum mismatch")
                if data.get("row_count") != len(frame):
                    raise ValueError("raw snapshot row-count mismatch")

            VersionedJSONStore(
                metadata_path,
                default={},
                validator=validate_metadata,
                quarantine_root=self.state_path / "quarantine",
                category="raw_snapshot_metadata",
            ).read()
            return frame
        except StateCorruptionError:
            raise
        except Exception as error:
            quarantine_path = quarantine_copy(
                path, self.state_path / "quarantine", "raw_snapshot"
            )
            raise StateCorruptionError(
                path, quarantine_path, error
            ) from error

    def _claim_history_revision(self, exchange: str) -> None:
        """Stamp the current adjustment revision on a history starting now."""

        try:
            from .history_revision import HistoryRevisionStore

            HistoryRevisionStore(self.base_path).claim_new_history(exchange)
        except Exception:
            # A missing stamp only costs an unnecessary rebuild prompt.  It
            # must never stop a download from publishing.
            pass

    def upsert(
        self,
        exchange: str,
        segment: str,
        target_date,
        rows: pd.DataFrame,
    ) -> int:
        """Upsert one daily equity frame into all affected symbol files."""

        if rows is None or rows.empty:
            return 0

        exchange = exchange.upper()
        self._claim_history_revision(exchange)
        ledger_path = self.state_path / "corporate_actions.json"
        if ledger_path.exists():
            from .corporate_actions import CorporateActionEngine

            CorporateActionEngine(
                self.base_path
            ).recover_incomplete_transactions()
        with self._lock:
            self.save_internal_snapshot(exchange, segment, target_date, rows)
            registry = self._read_registry()
            exchange_registry = registry["exchanges"].setdefault(exchange, {})
            action_records = self._read_applied_actions()
            written = 0

            for _, row in rows.iterrows():
                symbol = str(row.get("SYMBOL", "")).strip().upper()
                if not symbol:
                    continue
                stable_keys = self._stable_keys(row)
                self._ensure_symbol_filename(
                    registry, exchange, symbol, stable_keys
                )
                # The names this security was known by, captured before the
                # registry is re-pointed at today's name, so the action
                # replay can still match a ticker-keyed action recorded
                # under the other name.
                known = {symbol}
                for stable_key in stable_keys:
                    mapped = exchange_registry.get(stable_key)
                    if mapped:
                        known.add(mapped)
                for stable_key in stable_keys:
                    old_symbol = exchange_registry.get(stable_key)
                    if old_symbol and old_symbol != symbol:
                        self._merge_renamed_file(
                            registry, exchange, old_symbol, symbol
                        )
                    exchange_registry[stable_key] = symbol

                path = self._path_from_registry(registry, exchange, symbol)
                adjusted_row = self._apply_recorded_actions(
                    exchange, known, row, action_records
                )
                incoming = self._history_rows(pd.DataFrame([adjusted_row]))
                existing = self._read_history(path)
                combined = incoming if existing.empty else pd.concat(
                    [existing, incoming], ignore_index=True
                )
                self._write_history(path, combined)
                written += 1

            self._write_registry(registry)

        if ledger_path.exists():
            from .corporate_actions import CorporateActionEngine

            CorporateActionEngine(self.base_path).reconcile_pending(exchange)
        return written

    @staticmethod
    def _identity_column(frame: pd.DataFrame, name: str) -> List[Any]:
        """Return one identity column as plain values, or blanks if absent."""

        if name in frame.columns:
            return frame[name].tolist()
        return [""] * len(frame)

    @staticmethod
    def _canonical_order(frame: pd.DataFrame) -> Sequence[int]:
        """Return row positions in stable date order.

        Every canonical daily frame carries one validated date, so the common
        case needs no sort at all; the sort only exists for the mixed-date
        frames a rebuild or a test can hand over.
        """

        parsed = pd.to_datetime(
            frame["DATE"].astype(str), format="%Y%m%d", errors="coerce"
        ).reset_index(drop=True)
        if parsed.nunique(dropna=False) <= 1:
            return range(len(frame))
        return list(parsed.sort_values(kind="stable").index)

    def _plan_batch(
        self,
        registry: dict[str, Any],
        batch: List[HistoryBatchItem],
    ) -> _BatchPlan:
        """Resolve every row's destination without reading one history file.

        Renames make the registry order-dependent, so this stays a sequential
        walk in canonical order.  It reads only the three identity columns and
        keeps positions rather than rows, which is what lets a batch span many
        dates while the publish pass holds one symbol at a time.
        """

        contributions: dict[Path, list[tuple[int, int, str]]] = {}
        merge_sources: dict[Path, list[Path]] = {}
        retired: set[Path] = set()
        interned: dict[str, str] = {}
        refusals: list[MergeRefusal] = []
        dates_cache: dict[Path, Optional[set[str]]] = {}
        rows_seen = 0

        def stored_dates(path: Path) -> Optional[set[str]]:
            if path not in dates_cache:
                dates_cache[path] = self._history_dates(path)
            return dates_cache[path]

        def merge_overlap(*paths: Path) -> set[str]:
            """Dates claimed by more than one of the files a merge would fold.

            Only stored files count: the batch's own pending rows deduplicate
            against them by design.  An unreadable file contributes nothing
            here and fails loudly in the publish pass instead.
            """

            overlap: set[str] = set()
            union: set[str] = set()
            for member in dict.fromkeys(paths):
                dates = stored_dates(member)
                if not dates:
                    continue
                overlap |= union & dates
                union |= dates
            return overlap

        for index, item in enumerate(batch):
            exchange = item.exchange.upper()
            exchange_registry = registry["exchanges"].setdefault(exchange, {})
            frame = item.rows
            symbols = frame["SYMBOL"].tolist()
            isins = self._identity_column(frame, "ISIN")
            security_ids = self._identity_column(frame, "SECURITY_ID")

            for position in self._canonical_order(frame):
                symbol = str(symbols[position]).strip().upper()
                if not symbol:
                    continue
                rows_seen += 1
                symbol = interned.setdefault(symbol, symbol)
                stable_keys = self._stable_keys_from(
                    isins[position], security_ids[position]
                )
                self._ensure_symbol_filename(
                    registry, exchange, symbol, stable_keys
                )
                new_path = self._path_from_registry(registry, exchange, symbol)

                for stable_key in stable_keys:
                    old_symbol = exchange_registry.get(stable_key)
                    if old_symbol and old_symbol != symbol:
                        old_path = self._path_from_registry(
                            registry, exchange, old_symbol
                        )
                        overlap = merge_overlap(
                            old_path,
                            *merge_sources.get(old_path, []),
                            new_path,
                            *merge_sources.get(new_path, []),
                        ) if old_path != new_path else set()
                        if overlap:
                            # Not a rename: both histories stay, both keep
                            # their files and mappings, and the stable key
                            # follows the row that carries it today so the
                            # same merge is not re-attempted every batch.
                            refusals.append(self._refusal(
                                exchange, old_symbol, symbol,
                                old_path, new_path, overlap,
                            ))
                        elif old_path != new_path:
                            # The retired file's rows and its own pending
                            # merges lead, exactly as an incremental rename
                            # would have folded them in.
                            merge_sources[new_path] = [
                                *merge_sources.pop(old_path, []),
                                old_path,
                                *merge_sources.get(new_path, []),
                            ]
                            moved = contributions.pop(old_path, [])
                            if moved:
                                contributions[new_path] = [
                                    *moved,
                                    *contributions.get(new_path, []),
                                ]
                            retired.add(old_path)
                            retired.discard(new_path)
                            registry["files"].setdefault(exchange, {}).pop(
                                old_symbol.upper(), None
                            )
                    exchange_registry[stable_key] = symbol

                contributions.setdefault(new_path, []).append(
                    (index, position, symbol)
                )

        return _BatchPlan(
            contributions,
            merge_sources,
            retired,
            rows_seen,
            tuple(dict.fromkeys(refusals)),
        )

    @staticmethod
    def _contributing_entries(
        batch: List[HistoryBatchItem],
        pairs: Sequence[tuple[int, int, str]],
    ) -> tuple[tuple[str, str, date], ...]:
        """Name every date/segment whose rows were bound for one symbol file."""

        return tuple(dict.fromkeys(
            (
                batch[index].exchange.upper(),
                batch[index].segment.upper(),
                batch[index].target_date,
            )
            for index, _position, _symbol in pairs
        ))

    def _batch_incoming(
        self,
        batch: List[HistoryBatchItem],
        pairs: Sequence[tuple[int, int, str]],
        actions: _ActionIndex,
        aliases: dict[str, dict[str, str]],
    ) -> Optional[pd.DataFrame]:
        """Materialize one symbol's batched rows in canonical order."""

        if not pairs:
            return None
        frames: List[pd.DataFrame] = []
        current = pairs[0][0]
        positions: List[int] = []
        for index, position, _symbol in pairs:
            if index != current:
                frames.append(batch[current].rows.take(positions))
                current, positions = index, []
            positions.append(position)
        frames.append(batch[current].rows.take(positions))
        gathered = (
            frames[0].reset_index(drop=True)
            if len(frames) == 1
            else pd.concat(frames, ignore_index=True)
        )
        return self._history_rows(
            self._replay_actions(batch, pairs, gathered, actions, aliases)
        )

    @staticmethod
    def _alias_names(
        identifiers: Iterable[str],
        exchange_aliases: dict[str, str],
    ) -> set[str]:
        """Tickers the registry knew these identifiers by before this batch."""

        names = set()
        for value in identifiers:
            for key in (f"ISIN:{value}", f"ID:{value}"):
                mapped = exchange_aliases.get(key)
                if mapped:
                    names.add(mapped)
        return names

    def _replay_actions(
        self,
        batch: List[HistoryBatchItem],
        pairs: Sequence[tuple[int, int, str]],
        gathered: pd.DataFrame,
        actions: _ActionIndex,
        aliases: dict[str, dict[str, str]],
    ) -> pd.DataFrame:
        """Re-apply audited actions to re-downloaded rows.

        Skipped whole-symbol unless an audited action actually names this
        symbol, one of its identifiers, or the ticker its identifiers
        resolved to before this batch, because the replay is per row and
        reaches almost nothing.  ``aliases`` is the registry as it stood
        before this batch re-pointed it, so a re-downloaded pre-rename row
        still matches an action recorded under the current name.
        """

        exchange = batch[pairs[0][0]].exchange.upper()
        exchange_aliases = aliases.get(exchange, {})
        identifiers = {
            self._identifier(value)
            for column in ("ISIN", "SECURITY_ID")
            if column in gathered.columns
            for value in gathered[column].unique()
        }
        identifiers.discard("")
        symbols = {symbol for _i, _p, symbol in pairs}
        if not actions.touches(
            exchange,
            symbols | self._alias_names(identifiers, exchange_aliases),
            identifiers,
        ):
            return gathered

        def known_names(offset: int, symbol: str) -> set[str]:
            row = gathered.iloc[offset]
            row_identifiers = {
                key.split(":", 1)[1] for key in self._stable_keys(row)
            }
            return {symbol} | self._alias_names(
                row_identifiers, exchange_aliases
            )

        return pd.DataFrame([
            self._apply_recorded_actions(
                batch[index].exchange.upper(),
                known_names(offset, symbol),
                gathered.iloc[offset],
                actions.records,
            )
            for offset, (index, _position, symbol) in enumerate(pairs)
        ])

    def upsert_batch(
        self,
        items: Iterable[HistoryBatchItem],
        *,
        snapshots_saved: bool = False,
        completed_paths: Iterable[str] = (),
        on_symbol_written: Optional[Callable[[str], None]] = None,
        publish_files: bool = True,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> HistoryBatchResult:
        """Merge many dates with at most one read/write per final symbol.

        ``publish_files=False`` does everything except read and rewrite the
        symbol files: the registry is still re-pointed, renames still resolve
        their merge sources, retired files are still removed, and the result
        still names every symbol the batch settled.  It is for the Phase 5
        publication path, where the files are written afterwards out of the
        database instead -- extending each history rather than rewriting it,
        which on the owner's tree is 700 KB against 3,625 KB for the same
        change.  The rows are the same rows either way; step 2 proved the
        database reproduces this writer's output byte for byte.

        Destinations are planned first, then symbols are published one at a
        time, so peak memory follows the batch's own row count instead of the
        combined depth of every history the batch touches.

        ``completed_paths`` and ``on_symbol_written`` form the recovery hook
        used by the run journal. Replaying an uncheckpointed write is safe
        because date deduplication is deterministic.

        ``on_progress(done, total)`` is called as symbols are settled.  This
        stage runs after every download has finished and can take tens of
        seconds on a small tree and far longer on a deep one, and without it
        the interface has nothing to say for that whole time -- which reads as
        a hung application rather than as work in progress.

        A symbol that cannot be published is reported in
        ``HistoryBatchResult.failures`` rather than aborting the batch, so one
        unreadable history no longer holds back every other symbol.  Failures
        that are not per-symbol still raise: a renamed symbol whose merge
        cannot complete, because its rows live in a file this batch would then
        delete, and any run that exceeds
        ``MAX_ISOLATED_SYMBOL_FAILURES``, because that many failures is a
        problem with the store rather than with the symbols.
        """

        batch = [
            item for item in items
            if item.rows is not None and not item.rows.empty
        ]
        if not batch:
            return HistoryBatchResult(0, 0, 0, 0, 0)

        batch = sorted(
            batch,
            key=lambda item: (
                item.target_date,
                item.exchange.upper(),
                item.segment.upper(),
            ),
        )
        for exchange in {item.exchange.upper() for item in batch}:
            self._claim_history_revision(exchange)
        ledger_path = self.state_path / "corporate_actions.json"
        if ledger_path.exists():
            from .corporate_actions import CorporateActionEngine

            CorporateActionEngine(
                self.base_path
            ).recover_incomplete_transactions()

        completed = set(completed_paths)
        history_reads = 0
        history_writes = 0
        failures: List[HistorySymbolFailure] = []
        with self._lock:
            if not snapshots_saved:
                for item in batch:
                    self.save_internal_snapshot(
                        item.exchange,
                        item.segment,
                        item.target_date,
                        item.rows,
                    )

            registry = self._read_registry()
            actions = _ActionIndex.build(self._read_applied_actions())
            # Ticker aliases as they stood before this batch re-points the
            # registry, for the action replay only.
            aliases = {
                exchange: dict(mapping)
                for exchange, mapping in registry["exchanges"].items()
            } if actions.records else {}
            plan = self._plan_batch(registry, batch)

            def read_history(path: Path) -> pd.DataFrame:
                nonlocal history_reads
                history_reads += int(path.exists())
                return self._read_history(path)

            final_paths = (
                set(plan.contributions) | set(plan.merge_sources)
            ).difference(plan.retired)
            total = len(final_paths)
            if on_progress is not None:
                on_progress(0, total)
            for done, path in enumerate(sorted(final_paths, key=str), 1):
                relative = str(path.relative_to(self.base_path))
                if on_progress is not None:
                    on_progress(done, total)
                if relative in completed:
                    continue
                sources = plan.merge_sources.get(path, ())
                pairs = plan.contributions.get(path, ())
                if not publish_files:
                    # Neither the read nor the write happens; both exist only
                    # to produce the file, and something else is producing it.
                    history_writes += 1
                    if on_symbol_written is not None:
                        on_symbol_written(relative)
                    continue
                try:
                    frames = [read_history(source) for source in sources]
                    frames.append(read_history(path))
                    existing = (
                        frames[0] if len(frames) == 1
                        else pd.concat(frames, ignore_index=True)
                    )
                    incoming = self._batch_incoming(
                        batch, pairs, actions, aliases
                    )
                    if incoming is None:
                        history = existing
                    elif existing.empty:
                        history = incoming
                    else:
                        history = pd.concat(
                            [existing, incoming], ignore_index=True
                        )
                    if not path.exists() and len(history) == 1:
                        self._write_new_single_history(path, history)
                    else:
                        self._write_history(path, history)
                except Exception as error:
                    if sources or len(failures) >= MAX_ISOLATED_SYMBOL_FAILURES:
                        raise
                    failures.append(HistorySymbolFailure(
                        relative,
                        f"{type(error).__name__}: {error}",
                        self._contributing_entries(batch, pairs),
                    ))
                    continue
                history_writes += 1
                if on_symbol_written is not None:
                    on_symbol_written(relative)

            for old_path in sorted(
                plan.retired.difference(final_paths), key=str
            ):
                self._quarantine_superseded(old_path)
            self._write_registry(registry)

        if ledger_path.exists():
            from .corporate_actions import CorporateActionEngine

            CorporateActionEngine(self.base_path).reconcile_pending()
        return HistoryBatchResult(
            entries=len(batch),
            rows=plan.rows_seen,
            # Symbol files this batch leaves published, which is not the same
            # as the files it planned once a failure can be isolated.
            symbols=len(final_paths) - len(failures),
            history_reads=history_reads,
            history_writes=history_writes,
            failures=tuple(failures),
            refused_merges=plan.refusals,
        )

    def rewrite_symbol(
        self, exchange: str, symbol: str, history: pd.DataFrame
    ) -> Path:
        """Atomically replace one symbol after a corporate-action rebuild."""

        path = self.symbol_path(exchange, symbol)
        with self._lock:
            self._write_history(path, history)
        return path
