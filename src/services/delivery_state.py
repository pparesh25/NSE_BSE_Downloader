"""Persistent, crash-safe tracking for delivery reports published late."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from threading import Lock
from typing import List


class PendingDeliveryStore:
    """Store pending exchange/segment dates in the application's data folder."""

    _lock = Lock()

    def __init__(self, base_data_path: Path):
        self.state_dir = Path(base_data_path) / ".state"
        self.path = self.state_dir / "pending_delivery.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "pending": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data.get("pending"), dict):
                raise ValueError("invalid pending-delivery state")
            return data
        except Exception:
            return {"version": 1, "pending": {}}

    def _write(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

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
