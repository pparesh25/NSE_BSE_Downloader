import asyncio
import time

from src.services.pipeline_telemetry import PipelineTelemetry
from src.utils.stage_executor import BoundedStageExecutor


def test_bounded_stage_executor_runs_blocking_work_off_loop_and_records_stages():
    async def run():
        telemetry = PipelineTelemetry()
        executor = BoundedStageExecutor(
            name="prepare", max_workers=1, queue_size=1, telemetry=telemetry
        )
        await executor.start()
        started = time.monotonic()
        results = await asyncio.gather(*[
            executor.run(time.sleep, 0.01, stage="prepare")
            for _ in range(3)
        ])
        elapsed = time.monotonic() - started
        await executor.close()
        return results, elapsed, telemetry

    results, elapsed, telemetry = asyncio.run(run())
    assert results == [None, None, None]
    assert elapsed >= 0.025
    assert len(telemetry.durations("stage_finished")) == 3
    assert all(
        event.fields["executor"] == "prepare"
        for event in telemetry.events
        if event.kind == "stage_finished"
    )


def test_stage_executor_close_drains_queued_jobs():
    async def run():
        executor = BoundedStageExecutor(name="persist", max_workers=1, queue_size=1)
        first = asyncio.create_task(executor.run(time.sleep, 0.01))
        second = asyncio.create_task(executor.run(time.sleep, 0.01))
        await asyncio.gather(first, second)
        await executor.close()
        return executor._workers

    assert asyncio.run(run()) == []
