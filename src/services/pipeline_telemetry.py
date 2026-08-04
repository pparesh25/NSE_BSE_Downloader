"""Low-overhead, behavior-neutral pipeline observability primitives."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Optional


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
        self._listeners: list[Callable[[PipelineEvent], None]] = []
        self._lock = Lock()

    def subscribe(self, listener: Callable[[PipelineEvent], None]) -> None:
        """Receive future events; listener failures never affect the pipeline."""

        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[PipelineEvent], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def record(self, kind: str, **fields: Any) -> PipelineEvent:
        event = PipelineEvent(kind, time.monotonic_ns(), time.time(), fields)
        with self._lock:
            self.events.append(event)
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Observability must not replace or interrupt the real outcome.
                continue
        return event

    def export_jsonl(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with self._lock:
            events = tuple(self.events)
        with temporary.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")
        temporary.replace(path)

    def durations(self, kind: str) -> list[float]:
        with self._lock:
            events = tuple(self.events)
        return [
            float(event.fields["duration_ms"])
            for event in events
            if event.kind == kind and "duration_ms" in event.fields
        ]


@dataclass(frozen=True)
class PipelineStatusUpdate:
    exchange_segment: str
    message: str


class PipelineStatusPresenter:
    """Translate typed telemetry into concise user-facing live status."""

    STAGE_LABELS = {
        "downloaded": "Downloaded",
        "validated": "Validated",
        "daily": "Daily file ready",
        "symbols": "Symbol history",
        "delivery": "Delivery report",
        "actions": "Corporate actions",
        "combined": "Combined file",
    }

    @staticmethod
    def _segment(fields: dict[str, Any]) -> Optional[str]:
        value = fields.get("exchange_segment")
        return str(value) if value else None

    def present(self, event: PipelineEvent) -> Optional[PipelineStatusUpdate]:
        fields = event.fields
        segment = self._segment(fields)
        if event.kind == "download_attempt_started" and segment:
            return PipelineStatusUpdate(
                segment,
                f"Downloading {fields.get('date')} · attempt "
                f"{fields.get('attempt')}/{fields.get('max_attempts')}",
            )
        if event.kind == "retry_scheduled" and segment:
            delay = float(fields.get("delay_seconds", 0))
            return PipelineStatusUpdate(
                segment,
                f"Retry {fields.get('next_attempt')}/"
                f"{fields.get('max_attempts')} in {delay:.1f}s · "
                f"{fields.get('reason')} · {fields.get('date')}",
            )
        if event.kind == "pipeline_stage" and segment:
            stage = str(fields.get("stage", "stage"))
            status = str(fields.get("status", "pending"))
            label = self.STAGE_LABELS.get(stage, stage.replace("_", " ").title())
            suffix = {
                "pending": "queued",
                "complete": "complete",
                "failed": "failed",
                "skipped": "skipped",
                "disabled": "disabled",
            }.get(status, status)
            return PipelineStatusUpdate(
                segment,
                f"{label} {suffix} · {fields.get('date')}",
            )
        if event.kind in {"stage_queued", "stage_started", "stage_finished"}:
            raw_stage = str(fields.get("stage", ""))
            parts = raw_stage.split(":", 1)
            if len(parts) == 2 and "_" in parts[0]:
                action = parts[1].replace("_", " ").title()
                if event.kind == "stage_queued":
                    action += f" queued · queue depth {fields.get('queue_depth', 0)}"
                elif event.kind == "stage_started":
                    action += " started"
                else:
                    action += f" {fields.get('outcome', 'finished')}"
                return PipelineStatusUpdate(parts[0], action)
        if event.kind == "history_queued" and segment:
            return PipelineStatusUpdate(
                segment,
                f"History queued · {fields.get('target_date')} · "
                f"{fields.get('rows')} rows",
            )
        if event.kind == "date_join_finished":
            exchange = fields.get("exchange")
            if exchange:
                return PipelineStatusUpdate(
                    f"{exchange}_EQ",
                    f"Combined file {fields.get('status')} · "
                    f"{fields.get('date')} · {fields.get('rows')} rows",
                )
        return None


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
