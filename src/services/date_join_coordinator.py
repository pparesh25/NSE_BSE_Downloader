"""Per-date staged combined publication with bounded prepared-frame cache."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date
from threading import RLock
from typing import Optional

import pandas as pd

from .combined_file_builder import CombinedBuildResult, CombinedFileBuilder
from .source_resolver import is_available
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
        self.peak_cached_dates = 0

    @property
    def cached_dates(self) -> int:
        """Current prepared-frame date count for rollout evidence."""

        with self._lock:
            return len(self._cache)

    def dependencies_for(
        self, exchange: str, target_date: date
    ) -> tuple[str, ...]:
        """Dependencies that actually have a report for ``target_date``.

        A segment whose first-available date is later is not late -- it does not
        exist yet, and waiting for it holds the combined file back forever.  BSE
        INDEX begins on 2025-04-17, so with the shipped
        ``bse_index_append_to_eq`` default every earlier BSE EQ date used to end
        the run with no published file at all and be re-downloaded on every
        subsequent run.
        """

        exchange = exchange.upper()
        return tuple(
            segment
            for segment in self.dependencies.get(exchange, ())
            if is_available(exchange, segment, target_date)
        )

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
        configured = self.dependencies.get(exchange, ())
        if not configured or segment not in {"EQ", *configured}:
            return None
        dependencies = self.dependencies_for(exchange, target_date)
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
            self.peak_cached_dates = max(
                self.peak_cached_dates, len(self._cache)
            )

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
                if key in self._results or "EQ" not in ready:
                    continue
                if not self.dependencies.get(exchange, ()):
                    continue
                dependencies = self.dependencies_for(exchange, target_date)
                missing = [
                    segment for segment in dependencies if segment not in ready
                ]
                if missing:
                    result = self.builder.record_failure(
                        exchange,
                        target_date,
                        dependencies,
                        "Required staged component did not complete: "
                        + ", ".join(
                            f"{exchange}_{segment}" for segment in missing
                        ),
                    )
                else:
                    # Every dependency that exists for this date arrived, so the
                    # date is complete even though the configured list is
                    # shorter here than for a recent date.  Publish rather than
                    # fail; failing would leave no file and re-queue the date on
                    # every future run.
                    result = self.builder.reconcile_frames(
                        exchange,
                        target_date,
                        dependencies,
                        self._cache.get(key, {}),
                    )
                    self._cache.pop(key, None)
                self._results[key] = result
            return tuple(
                self._results[key] for key in sorted(self._results)
            )

    def result(
        self, exchange: str, target_date: date
    ) -> Optional[CombinedBuildResult]:
        return self._results.get((exchange.upper(), target_date))
