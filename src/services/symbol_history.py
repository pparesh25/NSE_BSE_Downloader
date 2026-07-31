"""Per-symbol text histories backed by canonical internal daily snapshots."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
import re
from threading import Lock
from typing import Dict, List, Optional

import pandas as pd

from .canonical_data import INTERNAL_EQUITY_COLUMNS, SYMBOL_HISTORY_COLUMNS


class SymbolHistoryStore:
    """Maintain deterministic NSE/BSE ``symbol.txt`` history files."""

    _lock = Lock()

    def __init__(self, base_data_path: Path):
        self.base_path = Path(base_data_path)
        self.state_path = self.base_path / ".state"
        self.registry_path = self.state_path / "symbol_registry.json"
        self.raw_path = self.state_path / "raw"

    @staticmethod
    def safe_filename(symbol: str) -> str:
        safe = re.sub(r"[^a-z0-9._-]+", "_", str(symbol).strip().lower())
        safe = safe.strip("._-")
        return safe or "unknown_symbol"

    def symbol_path(self, exchange: str, symbol: str) -> Path:
        return (
            self.base_path / exchange.upper() / "SYMBOLS" /
            f"{self.safe_filename(symbol)}.txt"
        )

    def resolve_symbol(self, exchange: str, stable_id: str) -> Optional[str]:
        """Resolve the latest ticker by ISIN/security code."""

        exchange_registry = self._read_registry().get(exchange.upper(), {})
        stable_id = str(stable_id).strip().upper()
        return (
            exchange_registry.get(f"ISIN:{stable_id}")
            or exchange_registry.get(f"ID:{stable_id}")
        )

    def _read_registry(self) -> Dict[str, Dict[str, str]]:
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_registry(self, registry: Dict[str, Dict[str, str]]) -> None:
        self.state_path.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.registry_path)

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
        except Exception:
            return pd.DataFrame(columns=SYMBOL_HISTORY_COLUMNS)
        for column in SYMBOL_HISTORY_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        return frame.loc[:, SYMBOL_HISTORY_COLUMNS]

    def _write_history(self, path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".txt.tmp")
        self._deduplicate(frame).to_csv(temporary, index=False)
        temporary.replace(path)

    def _merge_renamed_file(
        self, exchange: str, old_symbol: str, new_symbol: str
    ) -> None:
        old_path = self.symbol_path(exchange, old_symbol)
        new_path = self.symbol_path(exchange, new_symbol)
        if old_path == new_path or not old_path.exists():
            return
        combined = self._read_history(old_path)
        if new_path.exists():
            combined = pd.concat(
                [combined, self._read_history(new_path)], ignore_index=True
            )
        self._write_history(new_path, combined)
        old_path.unlink()

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
        if not ledger_path.exists():
            return []
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            return [
                record for record in ledger.get("actions", {}).values()
                if record.get("status") == "applied"
            ]
        except Exception:
            return []

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

        applicable = {}
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
        snapshot = rows.copy()
        for column in INTERNAL_EQUITY_COLUMNS:
            if column not in snapshot.columns:
                snapshot[column] = pd.NA
        snapshot.loc[:, INTERNAL_EQUITY_COLUMNS].to_csv(temporary, index=False)
        temporary.replace(path)
        return path

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
        with self._lock:
            self.save_internal_snapshot(exchange, segment, target_date, rows)
            registry = self._read_registry()
            exchange_registry = registry.setdefault(exchange, {})
            action_records = self._read_applied_actions()
            written = 0

            for _, row in rows.iterrows():
                symbol = str(row.get("SYMBOL", "")).strip().upper()
                if not symbol:
                    continue
                stable_keys = self._stable_keys(row)
                for stable_key in stable_keys:
                    old_symbol = exchange_registry.get(stable_key)
                    if old_symbol and old_symbol != symbol:
                        self._merge_renamed_file(exchange, old_symbol, symbol)
                    exchange_registry[stable_key] = symbol

                path = self.symbol_path(exchange, symbol)
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
            return written

    def rewrite_symbol(
        self, exchange: str, symbol: str, history: pd.DataFrame
    ) -> Path:
        """Atomically replace one symbol after a corporate-action rebuild."""

        path = self.symbol_path(exchange, symbol)
        with self._lock:
            self._write_history(path, history)
        return path
