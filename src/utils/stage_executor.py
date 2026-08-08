"""Bounded asynchronous stage executors for CPU/I/O-heavy pipeline work."""

from __future__ import annotations

import asyncio
import functools
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..services.pipeline_telemetry import PipelineTelemetry


@dataclass
class _StageJob:
    stage: str
    future: asyncio.Future[Any]
    function: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    queued_at: int


class BoundedStageExecutor:
    """Run synchronous stage functions with bounded queue backpressure."""

    def __init__(
        self,
        *,
        name: str,
        max_workers: int = 1,
        queue_size: int = 2,
        telemetry: Optional[PipelineTelemetry] = None,
    ):
        self.name = name
        self.max_workers = max(1, int(max_workers))
        self.queue_size = max(1, int(queue_size))
        self.telemetry = telemetry or PipelineTelemetry()
        self._queue: asyncio.Queue[Optional[_StageJob]] = asyncio.Queue(
            maxsize=self.queue_size
        )
        self._workers: list[asyncio.Task[None]] = []
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=f"nse-stage-{name}",
        )
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(), name=f"stage-{self.name}-{i}")
            for i in range(self.max_workers)
        ]

    async def run(
        self,
        function: Callable[..., Any],
        *args: Any,
        stage: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        if self._closed:
            raise RuntimeError(f"Stage executor {self.name} is closed")
        await self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        job = _StageJob(
            stage=stage or self.name,
            future=future,
            function=function,
            args=args,
            kwargs=kwargs,
            queued_at=time.monotonic_ns(),
        )
        queue_wait_started = time.monotonic_ns()
        await self._queue.put(job)
        self.telemetry.record(
            "stage_queued",
            executor=self.name,
            stage=job.stage,
            queue_depth=self._queue.qsize(),
            queue_wait_ms=(time.monotonic_ns() - queue_wait_started) / 1_000_000,
        )
        return await future

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.join()
        for _ in self._workers:
            await self._queue.put(None)
        if self._workers:
            await asyncio.gather(*self._workers)
        self._executor.shutdown(wait=True)
        self._workers.clear()

    async def _worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            started = time.monotonic_ns()
            self.telemetry.record(
                "stage_started",
                executor=self.name,
                stage=job.stage,
                queue_wait_ms=(started - job.queued_at) / 1_000_000,
            )
            try:
                result = await loop.run_in_executor(
                    self._executor,
                    functools.partial(job.function, *job.args, **job.kwargs),
                )
            except BaseException as error:
                if not job.future.cancelled():
                    job.future.set_exception(error)
                self.telemetry.record(
                    "stage_finished",
                    executor=self.name,
                    stage=job.stage,
                    outcome="error",
                    error_type=type(error).__name__,
                    duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                )
            else:
                if not job.future.cancelled():
                    job.future.set_result(result)
                self.telemetry.record(
                    "stage_finished",
                    executor=self.name,
                    stage=job.stage,
                    outcome="success",
                    duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                )
            finally:
                self._queue.task_done()
