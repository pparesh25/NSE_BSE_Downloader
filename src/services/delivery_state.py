"""Persistent, crash-safe tracking for delivery reports published late."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from threading import Lock
from typing import List

from .state_store import VersionedJSONStore


class PendingDeliveryStore:
    """Store pending exchange/segment dates in the application's data folder."""

    _lock = Lock()

    def __init__(self, base_data_path: Path):
        self.state_dir = Path(base_data_path) / ".state"
        self.path = self.state_dir / "pending_delivery.json"
        self._state = VersionedJSONStore(
            self.path,
            default={"version": 1, "pending": {}},
            validator=self._validate,
            quarantine_root=self.state_dir / "quarantine",
            category="pending_delivery",
        )

    @staticmethod
    def _validate(data: dict) -> None:
        if data.get("version") != 1:
            raise ValueError("unsupported pending-delivery state version")
        pending = data.get("pending")
        if not isinstance(pending, dict):
            raise ValueError("pending-delivery state must contain an object")
        for key, values in pending.items():
            if not isinstance(key, str) or not isinstance(values, list):
                raise ValueError("invalid pending-delivery entry")
            for value in values:
                if not isinstance(value, str):
                    raise ValueError("pending-delivery date must be text")
                date.fromisoformat(value)

    def _read(self) -> dict:
        return self._state.read()

    def _write(self, data: dict) -> None:
        self._state.write(data)

    def add(self, exchange: str, segment: str, target_date: date) -> None:
        key = f"{exchange.upper()}_{segment.upper()}"
        value = target_date.isoformat()
        with self._lock:
            data = self._read()
            dates = set(data["pending"].get(key, []))
            dates.add(value)
            data["pending"][key] = sorted(dates)
            self._write(data)

    def discard(self, exchange: str, segment: str, target_date: date) -> None:
        key = f"{exchange.upper()}_{segment.upper()}"
        value = target_date.isoformat()
        with self._lock:
            data = self._read()
            dates = set(data["pending"].get(key, []))
            dates.discard(value)
            if dates:
                data["pending"][key] = sorted(dates)
            else:
                data["pending"].pop(key, None)
            self._write(data)

    def dates(self, exchange: str, segment: str) -> List[date]:
        key = f"{exchange.upper()}_{segment.upper()}"
        with self._lock:
            raw_dates = self._read()["pending"].get(key, [])
        result = []
        for raw_date in raw_dates:
            try:
                result.append(date.fromisoformat(raw_date))
            except (TypeError, ValueError):
                continue
        return sorted(set(result))
