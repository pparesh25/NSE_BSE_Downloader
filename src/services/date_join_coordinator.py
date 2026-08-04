"""Per-date staged combined publication with bounded prepared-frame cache."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date
from threading import RLock
from typing import Optional

import pandas as pd

from .combined_file_builder import CombinedBuildResult, CombinedFileBuilder
from .pipeline_telemetry import PipelineTelemetry


class DateJoinCoordinator:
    """Join one date as soon as its configured components are durable."""

    def __init__(
        self,
        config,
        dependencies: dict[str, tuple[str, ...]],
        *,
        max_cache_dates: int = 4,
        telemetry: Optional[PipelineTelemetry] = None,
    ):
        self.builder = CombinedFileBuilder(config)
        self.dependencies = {
            exchange.upper(): tuple(segment.upper() for segment in segments)
            for exchange, segments in dependencies.items()
        }
        self.max_cache_dates = max(1, int(max_cache_dates))
        self.telemetry = telemetry or PipelineTelemetry()
        self._cache: OrderedDict[
            tuple[str, date], dict[str, pd.DataFrame]
        ] = OrderedDict()
        self._ready: dict[tuple[str, date], set[str]] = {}
        self._results: dict[tuple[str, date], CombinedBuildResult] = {}
        self._lock = RLock()

    def offer(
        self,
        exchange: str,
        segment: str,
        target_date: date,
        frame: pd.DataFrame,
    ) -> Optional[CombinedBuildResult]:
        """Record a durable component and publish when the date is ready."""

        exchange = exchange.upper()
        segment = segment.upper()
        dependencies = self.dependencies.get(exchange, ())
        if not dependencies or segment not in {"EQ", *dependencies}:
            return None
        key = (exchange, target_date)
        with self._lock:
            if key in self._results:
                return self._results[key]
            self._ready.setdefault(key, set()).add(segment)
            frames = self._cache.setdefault(key, {})
            frames[segment] = self.builder.lexical_frame(frame)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_cache_dates:
                self._cache.popitem(last=False)

            required = {"EQ", *dependencies}
            if not required.issubset(self._ready[key]):
                return None
            prepared = self._cache.get(key, {})
            result = self.builder.reconcile_frames(
                exchange, target_date, dependencies, prepared
            )
            self._results[key] = result
            self._cache.pop(key, None)
            self.telemetry.record(
                "date_join_finished",
                exchange=exchange,
                date=target_date.isoformat(),
                components=["EQ", *dependencies],
                status=result.status,
                rows=result.rows,
                sha256=result.sha256,
            )
            return result

    def finalize(self) -> tuple[CombinedBuildResult, ...]:
        """Fail unresolved EQ dates without replacing an existing public file."""

        with self._lock:
            for key, ready in sorted(self._ready.items()):
                exchange, target_date = key
                dependencies = self.dependencies.get(exchange, ())
                if key in self._results or "EQ" not in ready or not dependencies:
                    continue
                missing = [
                    segment for segment in dependencies if segment not in ready
                ]
                result = self.builder.record_failure(
                    exchange,
                    target_date,
                    dependencies,
                    "Required staged component did not complete: "
                    + ", ".join(f"{exchange}_{segment}" for segment in missing),
                )
                self._results[key] = result
            return tuple(
                self._results[key] for key in sorted(self._results)
            )

    def result(
        self, exchange: str, target_date: date
    ) -> Optional[CombinedBuildResult]:
        return self._results.get((exchange.upper(), target_date))
