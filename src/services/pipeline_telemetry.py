"""Low-overhead, behavior-neutral pipeline observability primitives."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class PipelineEvent:
    kind: str
    monotonic_ns: int
    wall_time: float
    fields: dict[str, Any] = field(default_factory=dict)


class PipelineTelemetry:
    """Append-only in-memory events; exporting is explicit and atomic."""

    def __init__(self) -> None:
        self.events: list[PipelineEvent] = []

    def record(self, kind: str, **fields: Any) -> PipelineEvent:
        event = PipelineEvent(kind, time.monotonic_ns(), time.time(), fields)
        self.events.append(event)
        return event

    def export_jsonl(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")
        temporary.replace(path)

    def durations(self, kind: str) -> list[float]:
        return [
            float(event.fields["duration_ms"])
            for event in self.events
            if event.kind == kind and "duration_ms" in event.fields
        ]


class EventLoopLagMonitor:
    """Sample asyncio timer delay without changing application decisions."""

    def __init__(self, telemetry: PipelineTelemetry, interval: float = 0.05):
        self.telemetry = telemetry
        self.interval = max(0.001, interval)
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._sample())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _sample(self) -> None:
        expected = time.monotonic() + self.interval
        while True:
            await asyncio.sleep(max(0.0, expected - time.monotonic()))
            now = time.monotonic()
            self.telemetry.record("event_loop_lag", lag_ms=max(0.0, (now - expected) * 1000))
            expected += self.interval
