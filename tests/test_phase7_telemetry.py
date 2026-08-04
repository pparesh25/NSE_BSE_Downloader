import asyncio
import json
from datetime import date
from types import SimpleNamespace

from src.core.config import Config
from src.services.pipeline_telemetry import (
    EventLoopLagMonitor,
    PipelineStatusPresenter,
    PipelineTelemetry,
)
from src.utils.async_downloader import AsyncDownloadManager, DownloadResult, DownloadTask


def _manager(telemetry):
    config = SimpleNamespace(
        pipeline_telemetry=telemetry,
        download_settings=SimpleNamespace(
            timeout_seconds=1, max_concurrent_downloads=1,
            retry_attempts=2, chunk_size=1024, rate_limit_delay=0,
        ),
    )
    return AsyncDownloadManager(config)


def test_attempt_telemetry_is_structured_and_does_not_change_retry_result():
    telemetry = PipelineTelemetry()
    manager = _manager(telemetry)
    task = DownloadTask(
        "https://example.test/data",
        "2026-08-04",
        date(2026, 8, 4),
        exchange_segment="NSE_EQ",
    )

    async def attempt(_task):
        return type("Result", (), {"success": True, "status_code": 200, "file_size": 3})()

    manager._attempt_download = attempt
    result = asyncio.run(manager.download_file(task))

    assert result.success
    assert [event.kind for event in telemetry.events] == [
        "download_attempt_started", "download_attempt_finished"
    ]
    assert telemetry.events[1].fields["duration_ms"] >= 0
    assert telemetry.events[0].fields["exchange_segment"] == "NSE_EQ"


def test_retry_telemetry_records_reason_and_delay():
    telemetry = PipelineTelemetry()
    manager = _manager(telemetry)
    manager._get_retry_delay = lambda *args, **kwargs: 0
    task = DownloadTask("https://example.test/data", "2026-08-04", date(2026, 8, 4))
    attempts = 0

    async def attempt(_task):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return DownloadResult(task=_task, success=False, error_message="HTTP 500", status_code=500)
        return DownloadResult(task=_task, success=True, file_data=b"ok", file_size=2)

    manager._attempt_download = attempt
    assert asyncio.run(manager.download_file(task)).success
    retry = next(event for event in telemetry.events if event.kind == "retry_scheduled")
    assert retry.fields["reason"] == "server_error"
    assert retry.fields["next_attempt"] == 2


def test_event_loop_lag_monitor_records_and_stops():
    async def run():
        telemetry = PipelineTelemetry()
        monitor = EventLoopLagMonitor(telemetry, interval=0.001)
        await monitor.start()
        await asyncio.sleep(0.005)
        await monitor.stop()
        return telemetry

    telemetry = asyncio.run(run())
    assert any(event.kind == "event_loop_lag" for event in telemetry.events)


def test_telemetry_export_is_jsonl(tmp_path):
    telemetry = PipelineTelemetry()
    telemetry.record("prepare", duration_ms=1.25, rows=4)
    path = tmp_path / "events.jsonl"
    telemetry.export_jsonl(path)
    assert json.loads(path.read_text())['kind'] == "prepare"


def test_telemetry_subscribers_are_non_fatal_and_removable():
    telemetry = PipelineTelemetry()
    received = []

    def broken(_event):
        raise RuntimeError("status display failed")

    telemetry.subscribe(broken)
    telemetry.subscribe(received.append)
    telemetry.record("first", value=1)
    telemetry.unsubscribe(received.append)
    telemetry.record("second", value=2)

    assert [event.kind for event in telemetry.events] == ["first", "second"]
    assert [event.kind for event in received] == ["first"]


def test_status_presenter_exposes_attempt_retry_queue_and_stage_outcome():
    telemetry = PipelineTelemetry()
    presenter = PipelineStatusPresenter()
    events = [
        telemetry.record(
            "download_attempt_started",
            exchange_segment="NSE_EQ",
            date="2026-08-04",
            attempt=1,
            max_attempts=3,
        ),
        telemetry.record(
            "retry_scheduled",
            exchange_segment="NSE_EQ",
            date="2026-08-04",
            next_attempt=2,
            max_attempts=3,
            delay_seconds=0.5,
            reason="server_error",
        ),
        telemetry.record(
            "stage_queued",
            stage="NSE_EQ:persist",
            queue_depth=2,
        ),
        telemetry.record(
            "stage_finished",
            stage="NSE_EQ:persist",
            outcome="success",
        ),
    ]

    messages = [presenter.present(event).message for event in events]
    assert messages == [
        "Downloading 2026-08-04 · attempt 1/3",
        "Retry 2/3 in 0.5s · server_error · 2026-08-04",
        "Persist queued · queue depth 2",
        "Persist success",
    ]


def test_pipeline_engine_defaults_to_staged_and_accepts_legacy(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""
data_paths:
  base_folder: "{base}"
download_settings: {{}}
download_options: {{}}
exchange_config: {{}}
""".format(base=tmp_path / "data"))
    assert Config(str(config_path)).pipeline_engine == "staged"

    config_path.write_text(
        config_path.read_text().replace(
            "download_options: {}",
            "download_options:\n  pipeline_engine: legacy",
        )
    )
    assert Config(str(config_path)).pipeline_engine == "legacy"

    config_path.write_text(
        config_path.read_text().replace(
            "pipeline_engine: legacy", "pipeline_engine: invalid"
        )
    )
    assert Config(str(config_path)).pipeline_engine == "staged"
