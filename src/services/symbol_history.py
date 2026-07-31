"""Per-symbol text histories backed by canonical internal daily snapshots."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
import re
import shutil
from threading import Lock
from typing import Any, List, Optional

import pandas as pd

from .canonical_data import INTERNAL_EQUITY_COLUMNS, SYMBOL_HISTORY_COLUMNS
from .state_store import (
    StateStoreError,
    VersionedJSONStore,
    default_corporate_ledger,
    file_sha256,
    migrate_corporate_ledger,
    quarantine_copy,
    validate_corporate_ledger,
)


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
        return {"version": 2, "exchanges": {}, "files": {}}

    @staticmethod
    def _migrate_registry(data: dict[str, Any]) -> dict[str, Any]:
        if "version" not in data:
            is_legacy = all(
                isinstance(exchange, str) and isinstance(values, dict)
                for exchange, values in data.items()
            )
            if is_legacy:
                return {
                    "version": 2,
                    "exchanges": data,
                    "files": {},
                }
        return data

    @staticmethod
    def _validate_registry(data: dict[str, Any]) -> None:
        if data.get("version") != 2:
            raise ValueError("unsupported symbol-registry version")
        exchanges = data.get("exchanges")
        files = data.get("files")
        if not isinstance(exchanges, dict) or not isinstance(files, dict):
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
        exchange = exchange.upper()
        symbol = str(symbol).strip().upper()
        mappings = registry["files"].setdefault(exchange, {})
        existing = mappings.get(symbol)
        if existing:
            return existing

        base = self.safe_filename(symbol)
        candidate = f"{base}.txt"
        used = {filename: owner for owner, filename in mappings.items()}
        if candidate in used and used[candidate] != symbol:
            identity = stable_keys[0] if stable_keys else symbol
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            width = 8
            candidate = f"{base}--{digest[:width]}.txt"
            while candidate in used and used[candidate] != symbol:
                width += 2
                candidate = f"{base}--{digest[:width]}.txt"
        mappings[symbol] = candidate
        return candidate

    @staticmethod
    def _history_rows(rows: pd.DataFrame) -> pd.DataFrame:
        frame = rows.copy()
        for column in SYMBOL_HISTORY_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        frame["DATE"] = pd.to_datetime(
            frame["DATE"].astype(str), format="%Y%m%d", errors="coerce"
        ).dt.strftime("%Y%m%d")
        return frame.loc[:, SYMBOL_HISTORY_COLUMNS]

    @staticmethod
    def _deduplicate(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        result = frame.copy()
        result["_volume_rank"] = pd.to_numeric(
            result["VOLUME"], errors="coerce"
        ).fillna(0)
        result = (
            result.sort_values(["DATE", "_volume_rank"], kind="stable")
            .drop_duplicates("DATE", keep="last")
            .sort_values("DATE", kind="stable")
            .drop(columns=["_volume_rank"])
        )
        return result.loc[:, SYMBOL_HISTORY_COLUMNS]

    def _read_history(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=SYMBOL_HISTORY_COLUMNS)
        try:
            frame = pd.read_csv(path, dtype=str)
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

    def _write_history(self, path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".txt.tmp")
        normalized = self._deduplicate(frame)
        self._validate_history(normalized)
        try:
            normalized.to_csv(temporary, index=False)
            if path.exists():
                try:
                    relative = path.relative_to(self.base_path)
                except ValueError:
                    relative = Path(path.name)
                backup = self.state_path / "backups" / "history" / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup_temporary = backup.with_name(backup.name + ".tmp")
                shutil.copy2(path, backup_temporary)
                backup_temporary.replace(backup)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _merge_renamed_file(
        self,
        registry: dict[str, Any],
        exchange: str,
        old_symbol: str,
        new_symbol: str,
    ) -> None:
        old_path = self._path_from_registry(registry, exchange, old_symbol)
        new_path = self._path_from_registry(registry, exchange, new_symbol)
        if old_path == new_path or not old_path.exists():
            return
        combined = self._read_history(old_path)
        if new_path.exists():
            combined = pd.concat(
                [combined, self._read_history(new_path)], ignore_index=True
            )
        self._write_history(new_path, combined)
        old_path.unlink()
        registry["files"].setdefault(exchange.upper(), {}).pop(
            old_symbol.upper(), None
        )

    @staticmethod
    def _stable_keys(row: pd.Series) -> List[str]:
        """Return every stable identifier available for the security.

        BSE corporate actions are keyed by security code while its price files
        also contain an ISIN.  Registering both avoids losing rename/action
        resolution by always preferring only one identifier.
        """

        keys = []
        isin = str(row.get("ISIN", "")).strip().upper()
        if isin and isin not in {"NAN", "<NA>"}:
            keys.append(f"ISIN:{isin}")
        security_id = str(row.get("SECURITY_ID", "")).strip().upper()
        if security_id and security_id not in {"NAN", "<NA>"}:
            keys.append(f"ID:{security_id}")
        return keys

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
        symbol: str,
        row: pd.Series,
        action_records: List[dict],
    ) -> pd.Series:
        """Reapply audited actions when a historical raw row is re-downloaded.

        Without this step, retrying delivery or repairing an old daily report
        could replace an already adjusted symbol-history row with raw OHLC.
        Only actions recorded as successfully applied are considered.
        """

        if not action_records:
            return row

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
            if stable_id not in identifiers and action_symbol != symbol.upper():
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
        adjusted = row.copy()
        # Match the engine's chronological, per-ex-date tick rounding so a raw
        # replay is byte-for-byte deterministic even across multiple actions.
        for ex_date in sorted(applicable):
            factor = applicable[ex_date]
            for column in ("OPEN", "HIGH", "LOW", "CLOSE"):
                value = pd.to_numeric(
                    pd.Series([adjusted.get(column)]), errors="coerce"
                ).iloc[0]
                if pd.notna(value):
                    adjusted[column] = round(
                        round(float(value) / factor / 0.05) * 0.05, 2
                    )
        return adjusted

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
            snapshot.to_csv(temporary, index=False)
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
        ledger_path = self.state_path / "corporate_actions.json"
        if ledger_path.exists():
            from .corporate_actions import CorporateActionEngine

            # Resolve a prior prepared transaction before a new daily row can
            # rename or revise the history file involved in that transaction.
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
                for stable_key in stable_keys:
                    old_symbol = exchange_registry.get(stable_key)
                    if old_symbol and old_symbol != symbol:
                        self._merge_renamed_file(
                            registry, exchange, old_symbol, symbol
                        )
                    exchange_registry[stable_key] = symbol

                path = self._path_from_registry(registry, exchange, symbol)
                adjusted_row = self._apply_recorded_actions(
                    exchange, symbol, row, action_records
                )
                incoming = self._history_rows(pd.DataFrame([adjusted_row]))
                existing = self._read_history(path)
                combined = incoming if existing.empty else pd.concat(
                    [existing, incoming], ignore_index=True
                )
                self._write_history(path, combined)
                written += 1

            self._write_registry(registry)

        # An action may have been recorded before its older price history was
        # downloaded.  Reconcile those audit records after the history/registry
        # lock is released so exactly-once transaction recovery cannot deadlock.
        if ledger_path.exists():
            from .corporate_actions import CorporateActionEngine

            CorporateActionEngine(self.base_path).reconcile_pending(exchange)
        return written

    def rewrite_symbol(
        self, exchange: str, symbol: str, history: pd.DataFrame
    ) -> Path:
        """Atomically replace one symbol after a corporate-action rebuild."""

        path = self.symbol_path(exchange, symbol)
        with self._lock:
            self._write_history(path, history)
        return path
