"""Repair symbol histories and registries from validated raw snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .canonical_data import INTERNAL_EQUITY_COLUMNS
from .corporate_actions import CorporateActionEngine
from .history_revision import HistoryRevisionStore
from .state_store import (
    StateCorruptionError,
    StateStoreError,
    VersionedJSONStore,
    file_sha256,
    quarantine_copy,
)
from .symbol_history import HistoryCorruptionError, SymbolHistoryStore


class SymbolHistoryRebuilder:
    """Deterministically rebuild symbol histories/registry from raw snapshots."""

    def __init__(self, base_data_path: Path):
        self.base_path = Path(base_data_path)
        self.histories = SymbolHistoryStore(self.base_path)

    def _read_snapshots(
        self, exchange: Optional[str] = None
    ) -> list[tuple[str, str, Path, pd.DataFrame]]:
        wanted = exchange.upper() if exchange else None
        snapshots: list[tuple[str, str, Path, pd.DataFrame]] = []
        for path in sorted(self.histories.raw_path.rglob("*.csv")):
            try:
                relative = path.relative_to(self.histories.raw_path)
                snapshot_exchange, segment, _ = relative.parts
                if wanted and snapshot_exchange.upper() != wanted:
                    continue
                frame = pd.read_csv(path, dtype=str)
                if list(frame.columns) != INTERNAL_EQUITY_COLUMNS:
                    raise ValueError("raw snapshot schema mismatch")

                metadata_path = path.with_suffix(".csv.meta.json")
                if metadata_path.exists():
                    actual_digest = file_sha256(path)

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
                        quarantine_root=(
                            self.histories.state_path / "quarantine"
                        ),
                        category="raw_snapshot_metadata",
                    ).read()

                snapshots.append(
                    (snapshot_exchange.upper(), segment.upper(), path, frame)
                )
            except StateCorruptionError:
                raise
            except Exception as error:
                quarantine_path = quarantine_copy(
                    path,
                    self.histories.state_path / "quarantine",
                    "raw_snapshot",
                )
                raise StateCorruptionError(
                    path, quarantine_path, error
                ) from error
        if not snapshots:
            scope = wanted or "all exchanges"
            raise StateStoreError(f"No raw snapshots are available for {scope}")
        return snapshots

    def _build_registry(
        self,
        snapshots: list[tuple[str, str, Path, pd.DataFrame]],
    ) -> dict[str, Any]:
        registry = self.histories._empty_registry()
        for exchange, _, _, frame in snapshots:
            exchange_registry = registry["exchanges"].setdefault(exchange, {})
            for _, row in frame.iterrows():
                symbol = str(row.get("SYMBOL", "")).strip().upper()
                if not symbol:
                    continue
                stable_keys = self.histories._stable_keys(row)
                self.histories._ensure_symbol_filename(
                    registry, exchange, symbol, stable_keys
                )
                for stable_key in stable_keys:
                    exchange_registry[stable_key] = symbol
        return registry

    def rebuild_registry(self) -> int:
        snapshots = self._read_snapshots()
        try:
            self.histories._read_registry()
        except StateCorruptionError:
            # Reading performs the quarantine copy. Rebuild is the explicit
            # authorization to replace damaged state with derived state.
            pass
        registry = self._build_registry(snapshots)
        self.histories._write_registry(registry)
        return sum(
            len(values) for values in registry["exchanges"].values()
        )

    def rebuild_symbol(self, exchange: str, symbol: str) -> Path:
        CorporateActionEngine(
            self.base_path
        ).recover_incomplete_transactions()
        exchange = exchange.upper()
        requested_symbol = str(symbol).strip().upper()
        snapshots = self._read_snapshots(exchange)
        # The registry is global. Rebuilding one exchange/symbol must retain
        # mappings for every other exchange represented by raw snapshots.
        registry = self._build_registry(self._read_snapshots())

        try:
            self.histories._read_registry()
        except StateCorruptionError:
            # Preserve invalid bytes before the explicit rebuild publishes the
            # registry derived from raw snapshots.
            pass

        # Seed the security's identity from the file the requested ticker
        # resolves to.  Seeding from every registry key that maps to the name
        # would conflate two companies the moment a delisted ticker was
        # reassigned, because the delisted company's stale key mapping still
        # names the same ticker.
        file_mappings = registry["files"].get(exchange, {})
        owners = registry["identities"].get(exchange, {})
        filename = file_mappings.get(requested_symbol)
        identifiers = set(owners.get(filename, ())) if filename else set()
        if not identifiers:
            identifiers = {
                stable_key
                for stable_key, mapped_symbol in registry["exchanges"]
                .get(exchange, {})
                .items()
                if mapped_symbol == requested_symbol
            }
        symbols = {requested_symbol}
        selected_rows: list[pd.Series] = []

        # Follow stable identifiers so renamed securities rebuild into one
        # continuous current-symbol history.  A row that carries identifiers
        # joins only through them: a ticker name is reassignable, so matching
        # an identifier-bearing row by name alone would swallow the history
        # of a different company that later took over the name.  Rows with no
        # identifiers at all (NSE SME, legacy eras) still join by name.
        changed = True
        while changed:
            changed = False
            selected_rows = []
            for snapshot_exchange, _, _, frame in snapshots:
                if snapshot_exchange != exchange:
                    continue
                for _, row in frame.iterrows():
                    row_symbol = str(row.get("SYMBOL", "")).strip().upper()
                    row_keys = set(self.histories._stable_keys(row))
                    matches = (
                        row_keys.intersection(identifiers)
                        if row_keys
                        else row_symbol in symbols
                    )
                    if matches:
                        selected_rows.append(row)
                        old_symbol_count = len(symbols)
                        old_identifier_count = len(identifiers)
                        symbols.add(row_symbol)
                        identifiers.update(row_keys)
                        changed = changed or (
                            len(symbols) != old_symbol_count
                            or len(identifiers) != old_identifier_count
                        )

        if not selected_rows:
            raise StateStoreError(
                f"No raw snapshot rows found for {exchange} {requested_symbol}"
            )

        selected = pd.DataFrame(selected_rows)
        selected["_parsed_date"] = pd.to_datetime(
            selected["DATE"].astype(str), format="%Y%m%d", errors="coerce"
        )
        if selected["_parsed_date"].isna().any():
            raise StateStoreError(
                f"Raw snapshots contain invalid dates for {requested_symbol}"
            )
        selected = selected.sort_values("_parsed_date", kind="stable")
        current_symbol = str(selected.iloc[-1]["SYMBOL"]).strip().upper()
        selected = selected.drop(columns=["_parsed_date"])

        stable_keys = sorted(identifiers)
        self.histories._ensure_symbol_filename(
            registry, exchange, current_symbol, stable_keys
        )
        for stable_key in stable_keys:
            registry["exchanges"].setdefault(exchange, {})[
                stable_key
            ] = current_symbol
        action_records = self.histories._read_applied_actions()
        # Every name the security was known by matches, not only the current
        # one: an action whose stable_id fell back to the pre-rename ticker
        # would otherwise be skipped and the rebuild would revert it.
        adjusted_rows = [
            self.histories._apply_recorded_actions(
                exchange, symbols, row, action_records
            )
            for _, row in selected.iterrows()
        ]
        history = self.histories._history_rows(pd.DataFrame(adjusted_rows))
        history = self.histories._deduplicate(history)
        path = self.histories._path_from_registry(
            registry, exchange, current_symbol
        )
        if path.exists():
            try:
                self.histories._read_history(path)
            except HistoryCorruptionError:
                pass
        self.histories._write_history(path, history)
        self.histories._write_registry(registry)
        return path

    def rebuild_exchange(self, exchange: str) -> list[Path]:
        exchange = exchange.upper()
        snapshots = self._read_snapshots(exchange)
        symbols = {
            str(row.get("SYMBOL", "")).strip().upper()
            for snapshot_exchange, _, _, frame in snapshots
            if snapshot_exchange == exchange
            for _, row in frame.iterrows()
            if str(row.get("SYMBOL", "")).strip()
        }
        self.rebuild_registry()
        paths = [
            self.rebuild_symbol(exchange, symbol)
            for symbol in sorted(symbols)
        ]
        # Every symbol this exchange has raw snapshots for was just replayed
        # through the current arithmetic, so the rebuild prompt can stop.
        HistoryRevisionStore(self.base_path).mark_current(exchange)
        return paths

    def rebuild_all(self) -> dict[str, list[Path]]:
        snapshots = self._read_snapshots()
        exchanges = sorted({exchange for exchange, _, _, _ in snapshots})
        return {
            exchange: self.rebuild_exchange(exchange)
            for exchange in exchanges
        }
