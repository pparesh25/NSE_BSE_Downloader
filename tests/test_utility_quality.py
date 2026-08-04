import asyncio
from datetime import date, datetime, timezone
import zipfile

import pandas as pd
import pytest

from src.core.exceptions import (
    DataProcessingError,
    DateRangeError,
    DownloaderError,
    FileOperationError,
    GUIError,
    MemoryError,
    NetworkError,
    ValidationError,
)
from src.utils.date_utils import DateUtils
from src.utils.file_utils import FileUtils
from src.utils import http_client
from src.utils.http_client import HTTPStatusError
from src.utils.memory_optimizer import MemoryOptimizer


class _Content:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class _Response:
    def __init__(self, status=200, text="ok", data=b"ok", chunks=()):
        self.status = status
        self._text = text
        self._data = data
        self.headers = {
            "content-length": str(sum(len(chunk) for chunk in chunks))
        }
        self.content = _Content(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def text(self):
        return self._text

    async def read(self):
        return self._data


class _Session:
    def __init__(self, response):
        self.response = response
        self.closed = False
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self.response

    async def close(self):
        self.closed = True


def test_file_utilities_cover_atomic_local_file_workflows(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("market-data", encoding="utf-8")
    assert FileUtils.get_file_size(source) == 11
    assert FileUtils.validate_file_exists(source, min_size=5)
    assert not FileUtils.validate_file_exists(source, min_size=50)
    assert not FileUtils.validate_file_exists(tmp_path / "missing")

    copied = tmp_path / "nested" / "copied.txt"
    FileUtils.copy_file(source, copied)
    moved = tmp_path / "moved.txt"
    FileUtils.move_file(copied, moved)
    renamed = FileUtils.change_file_extension(moved, "csv")
    assert renamed.read_text(encoding="utf-8") == "market-data"

    archive = tmp_path / "reports.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("daily/report.csv", "SYMBOL,CLOSE\nABC,10\n")
    extracted = FileUtils.extract_zip_file(archive, tmp_path / "extracted")
    assert extracted == [tmp_path / "extracted" / "daily" / "report.csv"]
    assert extracted[0].exists()

    cleanup = tmp_path / "cleanup"
    (cleanup / "folder").mkdir(parents=True)
    (cleanup / "folder" / "temp").write_text("x", encoding="utf-8")
    (cleanup / "keep.txt").write_text("keep", encoding="utf-8")
    (cleanup / "remove.txt").write_text("remove", encoding="utf-8")
    FileUtils.cleanup_directory(cleanup, keep_files=["keep.txt"])
    assert [item.name for item in cleanup.iterdir()] == ["keep.txt"]
    FileUtils.delete_file(cleanup / "keep.txt")
    FileUtils.cleanup_directory(tmp_path / "not-created")
    assert not (cleanup / "keep.txt").exists()


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (lambda base: FileUtils.copy_file(base / "none", base / "copy"), "copy_file"),
        (lambda base: FileUtils.move_file(base / "none", base / "move"), "move_file"),
        (lambda base: FileUtils.change_file_extension(base / "none", ".csv"), "change_extension"),
        (lambda base: FileUtils.extract_zip_file(base / "none", base / "out"), "extract_zip"),
    ],
)
def test_file_utilities_report_operation_context(tmp_path, operation, expected):
    with pytest.raises(FileOperationError) as captured:
        operation(tmp_path)
    assert captured.value.operation == expected
    assert "File:" in str(captured.value)
    assert FileUtils.get_file_size(tmp_path / "none") == 0


def test_custom_errors_preserve_actionable_context():
    assert str(DownloaderError("failed", {"row": 3})) == (
        "failed (Details: {'row': 3})"
    )
    assert "File: report.csv" in str(
        DataProcessingError("invalid", "report.csv", "bad schema")
    )
    assert str(NetworkError("offline", "https://nse.test", 503)).endswith(
        "(URL: https://nse.test) (Status: 503)"
    )
    assert "Operation: write" in str(
        FileOperationError("denied", "out.txt", "write")
    )
    assert "Start: 2026-01-02, End: 2026-01-01" in str(
        DateRangeError("reversed", "2026-01-02", "2026-01-01")
    )
    assert str(MemoryError("pressure", "90%")) == "pressure (Memory: 90%)"
    assert str(ValidationError("bad", "CLOSE", 0)) == (
        "bad (Field: CLOSE, Value: 0)"
    )
    assert str(GUIError("missing", "donateButton")) == (
        "missing (Widget: donateButton)"
    )


def test_async_http_helpers_validate_status_stream_size_and_progress(tmp_path):
    async def scenario():
        text_session = _Session(_Response(text="calendar"))
        assert await http_client.fetch_text(
            "https://example.test/calendar", session=text_session
        ) == "calendar"

        byte_session = _Session(_Response(data=b"archive"))
        assert await http_client.fetch_bytes(
            "https://example.test/archive", session=byte_session
        ) == b"archive"

        progress = []
        target = tmp_path / "download.bin"
        stream_session = _Session(_Response(chunks=(b"abc", b"", b"def")))
        await http_client.download_to_file(
            "https://example.test/file",
            str(target),
            session=stream_session,
            progress=lambda done, total: progress.append((done, total)),
            max_bytes=6,
        )
        assert target.read_bytes() == b"abcdef"
        assert progress == [(3, 6), (6, 6)]

        with pytest.raises(HTTPStatusError) as missing:
            await http_client.fetch_text(
                "https://example.test/missing",
                session=_Session(_Response(status=404)),
            )
        assert missing.value.status == 404

        with pytest.raises(HTTPStatusError) as unavailable:
            await http_client.fetch_bytes(
                "https://example.test/error",
                session=_Session(_Response(status=503, text="service unavailable")),
            )
        assert unavailable.value.message == "service unavailable"

        with pytest.raises(ValueError, match="maximum size"):
            await http_client.download_to_file(
                "https://example.test/large",
                str(tmp_path / "large.bin"),
                session=_Session(_Response(chunks=(b"12345",))),
                max_bytes=4,
            )

    asyncio.run(scenario())


def test_http_owned_session_and_sync_wrappers_close_resources(
    tmp_path, monkeypatch
):
    sessions = []

    async def fake_session(_timeout=None, _headers=None):
        session = _Session(_Response(text="owned", data=b"bytes", chunks=(b"x",)))
        sessions.append(session)
        return session

    monkeypatch.setattr(http_client, "_single_use_session", fake_session)
    assert http_client.fetch_text_sync("https://example.test") == "owned"
    assert http_client.fetch_bytes_sync("https://example.test") == b"bytes"
    output = tmp_path / "sync.bin"
    http_client.download_to_file_sync("https://example.test", str(output))
    assert output.read_bytes() == b"x"
    assert len(sessions) == 3
    assert all(session.closed for session in sessions)


def test_memory_optimizer_chunks_transforms_and_reports(tmp_path, monkeypatch):
    usage = {
        "rss_mb": 10.0,
        "vms_mb": 20.0,
        "percent": 1.0,
        "available_mb": 100.0,
        "total_mb": 200.0,
        "system_percent": 25.0,
    }
    monkeypatch.setattr(MemoryOptimizer, "_get_memory_usage", lambda self: usage.copy())
    optimizer = MemoryOptimizer(chunk_size=2, memory_threshold=80)

    frame = pd.DataFrame({
        "symbol": ["ABC", "ABC", "ABC", "ABC"],
        "volume": pd.Series([1, 2, 3, 4], dtype="int64"),
        "close": [10.25, 11.5, 12.75, 13.0],
    })
    optimized = optimizer.optimize_dataframe(frame)
    assert str(optimized["symbol"].dtype) == "category"
    assert optimized["volume"].dtype.itemsize < frame["volume"].dtype.itemsize
    assert optimized["close"].dtype == frame["close"].dtype

    source = tmp_path / "source.csv"
    frame.to_csv(source, index=False)
    chunks = list(optimizer.read_csv_chunked(source))
    assert [len(chunk) for chunk in chunks] == [2, 2]

    output = tmp_path / "result" / "output.csv"
    stats = optimizer.process_large_csv(
        source,
        lambda chunk: chunk.assign(volume=chunk["volume"] * 10),
        output,
    )
    assert stats["total_rows"] == 4
    assert stats["chunks_processed"] == 2
    assert stats["errors"] == []
    assert pd.read_csv(output)["volume"].tolist() == [10, 20, 30, 40]

    estimate = optimizer.estimate_csv_memory_usage(source, sample_rows=2)
    assert estimate["sample_rows"] == 2
    assert estimate["file_size_mb"] > 0
    assert "error" in optimizer.estimate_csv_memory_usage(tmp_path / "missing.csv")

    summary = optimizer.get_memory_summary()
    assert summary["memory_increase_mb"] == 0
    assert not summary["above_threshold"]


def test_memory_threshold_and_processing_failures_are_safe(tmp_path, monkeypatch):
    low = {
        "rss_mb": 10.0, "vms_mb": 0.0, "percent": 0.0,
        "available_mb": 0.0, "total_mb": 0.0, "system_percent": 20.0,
    }
    high = {**low, "rss_mb": 12.0, "system_percent": 95.0}
    monkeypatch.setattr(MemoryOptimizer, "_get_memory_usage", lambda self: low.copy())
    optimizer = MemoryOptimizer(memory_threshold=80)
    monkeypatch.setattr(optimizer, "_get_memory_usage", lambda: high.copy())
    collections = []
    monkeypatch.setattr(
        optimizer, "force_garbage_collection", lambda: collections.append(True) or {}
    )
    optimizer._check_memory_threshold()
    assert collections == [True]
    assert optimizer.peak_memory["rss_mb"] == 12.0

    with pytest.raises(DataProcessingError, match="chunks"):
        list(optimizer.read_csv_chunked(tmp_path / "missing.csv"))


def test_date_helpers_cover_url_calendar_and_market_boundaries():
    friday = date(2026, 7, 31)
    saturday = date(2026, 8, 1)
    monday = date(2026, 8, 3)
    holiday = {monday}
    assert not DateUtils.is_weekend(friday)
    assert DateUtils.is_weekend(saturday)
    assert DateUtils.is_holiday(monday, holiday)
    assert not DateUtils.is_trading_day(monday, holidays=holiday)
    assert DateUtils.get_trading_days(friday, monday) == [friday, monday]
    assert DateUtils.format_date_for_url(friday, "%Y%m%d") == "20260731"
    assert DateUtils.parse_date_from_filename(
        "BhavCopy_20260731.csv", "%Y%m%d"
    ) == friday
    assert DateUtils.parse_date_from_filename("no-date.csv", "%Y%m%d") is None
    assert DateUtils.parse_date_from_filename("20261340.csv", "%Y%m%d") is None
    assert DateUtils.get_last_trading_day(saturday) == friday
    assert DateUtils.get_next_trading_day(friday) == monday
    assert DateUtils.calculate_trading_days_count(friday, monday) == 2
    assert DateUtils.add_trading_days(friday, 1) == monday
    assert len(DateUtils.get_month_trading_days(2026, 8)) == 21

    before_open = datetime(2026, 8, 3, 3, 30, tzinfo=timezone.utc)
    during_market = datetime(2026, 8, 3, 5, 0, tzinfo=timezone.utc)
    after_close = datetime(2026, 8, 3, 11, 0, tzinfo=timezone.utc)
    assert not DateUtils.is_market_hours(before_open)
    assert DateUtils.is_market_hours(during_market)
    assert not DateUtils.is_market_hours(after_close)
    assert not DateUtils.is_data_available_time(after_close)
    assert DateUtils.get_expected_last_trading_date(now=after_close) == friday

    naive = datetime(2026, 8, 3, 10, 0)
    assert DateUtils.now_ist(naive).tzinfo == DateUtils.MARKET_TIMEZONE
