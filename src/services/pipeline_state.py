"""Crash-safe per-date pipeline state and structured download results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
import shutil
from threading import Lock
from typing import Any, Iterable, Optional

from .pipeline_sqlite import SQLitePipelineStore
from .state_store import VersionedJSONStore


PIPELINE_STAGES = (
    "downloaded",
    "validated",
    "daily",
    "symbols",
    "delivery",
    "actions",
    "combined",
)
STAGE_STATUSES = {"pending", "complete", "failed", "skipped", "disabled"}
StageUpdate = tuple[str, str, date, str, str, dict[str, Any]]


@dataclass(frozen=True)
class DateResult:
    """Outcome for one requested market date."""

    target_date: date
    status: str
    completed_stages: tuple[str, ...] = ()
    failed_stages: tuple[str, ...] = ()
    error: Optional[str] = None
    output_path: Optional[str] = None


@dataclass(frozen=True)
class SegmentResult:
    """Structured result for one exchange segment and date range."""

    exchange: str
    segment: str
    dates: tuple[DateResult, ...] = field(default_factory=tuple)

    @property
    def success_count(self) -> int:
        return sum(item.status == "success" for item in self.dates)

    @property
    def partial_count(self) -> int:
        return sum(item.status == "partial" for item in self.dates)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.dates)

    @property
    def skipped_count(self) -> int:
        return sum(item.status == "skipped" for item in self.dates)

    @property
    def ok(self) -> bool:
        return bool(self.dates) and all(
            item.status in {"success", "skipped"} for item in self.dates
        )

    @property
    def any_success(self) -> bool:
        return any(item.status == "success" for item in self.dates)

    def summary(self) -> str:
        return (
            f"{self.exchange}_{self.segment}: {self.success_count} successful, "
            f"{self.partial_count} partial, {self.failed_count} failed, "
            f"{self.skipped_count} skipped"
        )


class PipelineManifest:
    """Persist stage completion so interrupted dates can be resumed safely."""

    _lock = Lock()

    def __init__(self, base_data_path: Path):
        state_dir = Path(base_data_path) / ".state"
        self.path = state_dir / "pipeline_manifest.json"
        self.legacy_backup_path = state_dir / "pipeline_manifest.pre_sqlite.json"
        self.database_path = state_dir / "pipeline_state.sqlite3"
        self._legacy_state = VersionedJSONStore(
            self.path,
            default={"version": 1, "dates": {}},
            validator=self._validate,
            quarantine_root=state_dir / "quarantine",
            category="pipeline_manifest",
        )
        self._state = SQLitePipelineStore(
            self.database_path,
            self._validate,
            state_dir / "quarantine",
        )
        self._import_legacy_json()

    def _import_legacy_json(self) -> None:
        """Validate and import legacy JSON once, leaving its bytes untouched."""

        if self._state.legacy_imported() or not self.path.is_file():
            return
        data = self._legacy_state.read()
        if not self.legacy_backup_path.exists():
            self.legacy_backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, self.legacy_backup_path)
        self._state.import_manifest(data)

    def manifest_data(self) -> dict[str, Any]:
        """Return a validated compatibility-shaped snapshot for verification."""

        with self._lock:
            return self._state.export_manifest()

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        if data.get("version") != 1:
            raise ValueError("unsupported pipeline manifest version")
        records = data.get("dates")
        if not isinstance(records, dict):
            raise ValueError("pipeline manifest dates must be an object")
        for key, record in records.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                raise ValueError("invalid pipeline date record")
            exchange = record.get("exchange")
            segment = record.get("segment")
            raw_date = record.get("date")
            if (
                not isinstance(exchange, str) or not exchange
                or not isinstance(segment, str) or not segment
                or not isinstance(raw_date, str) or not raw_date
            ):
                raise ValueError("pipeline record identity is invalid")
            date.fromisoformat(raw_date)
            if key != f"{exchange}_{segment}:{raw_date}":
                raise ValueError("pipeline record key does not match identity")
            stages = record.get("stages")
            required = record.get("required_stages")
            if not isinstance(stages, dict) or not isinstance(required, list):
                raise ValueError("pipeline stages are invalid")
            if not set(required).issubset(PIPELINE_STAGES):
                raise ValueError("pipeline required stage is unknown")
            for stage, stage_record in stages.items():
                if stage not in PIPELINE_STAGES or not isinstance(stage_record, dict):
                    raise ValueError("pipeline stage record is invalid")
                if stage_record.get("status") not in STAGE_STATUSES:
                    raise ValueError("pipeline stage status is invalid")
            if not isinstance(record.get("complete"), bool):
                raise ValueError("pipeline completion flag is invalid")

    @staticmethod
    def _key(exchange: str, segment: str, target_date: date) -> str:
        return f"{exchange.upper()}_{segment.upper()}:{target_date.isoformat()}"

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _refresh(record: dict[str, Any]) -> None:
        stages = record["stages"]
        record["complete"] = all(
            stages.get(stage, {}).get("status") in {"complete", "skipped"}
            for stage in record["required_stages"]
        )
        record["updated_at"] = PipelineManifest._timestamp()

    def begin(
        self,
        exchange: str,
        segment: str,
        target_date: date,
        required_stages: Iterable[str],
        disabled_stages: Iterable[str] = (),
    ) -> None:
        required = list(dict.fromkeys(required_stages))
        unknown = set(required).difference(PIPELINE_STAGES)
        if unknown:
            raise ValueError(f"Unknown pipeline stages: {sorted(unknown)}")
        disabled = set(disabled_stages)
        key = self._key(exchange, segment, target_date)

        def update(
            records: dict[str, Optional[dict[str, Any]]]
        ) -> None:
            record = records[key]
            if record is None:
                record = {
                    "exchange": exchange.upper(),
                    "segment": segment.upper(),
                    "date": target_date.isoformat(),
                    "required_stages": required,
                    "stages": {},
                    "complete": False,
                    "updated_at": self._timestamp(),
                }
                records[key] = record
            # A before-market-close skip is run-specific.  Once a later run
            # actually starts this date it must be eligible to complete.
            record.pop("skipped_reason", None)
            record["required_stages"] = required
            for stage in PIPELINE_STAGES:
                current = record["stages"].get(stage, {})
                if stage in disabled:
                    current = {"status": "disabled"}
                elif current.get("status") == "disabled" and stage in required:
                    current = {"status": "pending"}
                elif not current:
                    current = {"status": "pending"}
                record["stages"][stage] = current
            self._refresh(record)

        with self._lock:
            self._state.update_records((key,), update)

    def mark(
        self,
        exchange: str,
        segment: str,
        target_date: date,
        stage: str,
        status: str,
        **metadata: Any,
    ) -> None:
        self.mark_many((
            (exchange, segment, target_date, stage, status, metadata),
        ))

    def mark_many(self, updates: Iterable[StageUpdate]) -> None:
        """Apply multiple stage transitions in one durable transaction."""

        prepared = list(updates)
        for _, _, _, stage, status, _ in prepared:
            if stage not in PIPELINE_STAGES or status not in STAGE_STATUSES:
                raise ValueError(
                    f"Invalid pipeline stage update: {stage}={status}"
                )
        if not prepared:
            return
        keyed = [
            (
                self._key(exchange, segment, target_date),
                stage,
                status,
                metadata,
            )
            for exchange, segment, target_date, stage, status, metadata
            in prepared
        ]

        def update(
            records: dict[str, Optional[dict[str, Any]]]
        ) -> None:
            for key, stage, status, metadata in keyed:
                record = records[key]
                if record is None:
                    raise ValueError(
                        f"Pipeline date was not started: {key}"
                    )
                stage_record = {
                    "status": status,
                    "updated_at": self._timestamp(),
                }
                for name, value in metadata.items():
                    if value is not None:
                        stage_record[name] = value
                record["stages"][stage] = stage_record
                self._refresh(record)

        with self._lock:
            self._state.update_records(
                (key for key, _, _, _ in keyed), update
            )

    def has_date(
        self, exchange: str, segment: str, target_date: date
    ) -> bool:
        key = self._key(exchange, segment, target_date)
        with self._lock:
            return self._state.has_record(key)

    def require_stage(
        self, exchange: str, segment: str, target_date: date, stage: str
    ) -> None:
        """Make a stage part of completion and reset it for reconciliation."""

        if stage not in PIPELINE_STAGES:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        key = self._key(exchange, segment, target_date)

        def update(
            records: dict[str, Optional[dict[str, Any]]]
        ) -> None:
            record = records[key]
            if record is None:
                raise ValueError(f"Pipeline date was not started: {key}")
            if stage not in record["required_stages"]:
                record["required_stages"].append(stage)
            record["stages"][stage] = {
                "status": "pending",
                "updated_at": self._timestamp(),
            }
            self._refresh(record)

        with self._lock:
            self._state.update_records((key,), update)

    def disable_stage(
        self, exchange: str, segment: str, target_date: date, stage: str
    ) -> None:
        """Remove a disabled optional stage from the completion requirement."""

        if stage not in PIPELINE_STAGES:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        key = self._key(exchange, segment, target_date)

        def update(
            records: dict[str, Optional[dict[str, Any]]]
        ) -> None:
            record = records[key]
            if record is None:
                raise ValueError(f"Pipeline date was not started: {key}")
            record["required_stages"] = [
                value for value in record["required_stages"] if value != stage
            ]
            record["stages"][stage] = {
                "status": "disabled",
                "updated_at": self._timestamp(),
            }
            self._refresh(record)

        with self._lock:
            self._state.update_records((key,), update)

    def skip_date(
        self, exchange: str, segment: str, target_date: date, reason: str
    ) -> None:
        self.begin(exchange, segment, target_date, ())
        key = self._key(exchange, segment, target_date)

        def update(
            records: dict[str, Optional[dict[str, Any]]]
        ) -> None:
            record = records[key]
            if record is None:  # pragma: no cover - begin guarantees the row
                raise ValueError(f"Pipeline date was not started: {key}")
            record["skipped_reason"] = reason
            record["complete"] = True
            record["updated_at"] = self._timestamp()

        with self._lock:
            self._state.update_records((key,), update)

    def incomplete_dates(self, exchange: str, segment: str) -> list[date]:
        with self._lock:
            return self._state.incomplete_dates(exchange, segment)

    def date_result(
        self, exchange: str, segment: str, target_date: date
    ) -> DateResult:
        key = self._key(exchange, segment, target_date)
        with self._lock:
            record = self._state.get_record(key)
        if record is None:
            return DateResult(target_date, "failed", error="No pipeline record")
        if record.get("skipped_reason"):
            return DateResult(
                target_date, "skipped", error=record["skipped_reason"]
            )
        completed = tuple(
            stage for stage, value in record["stages"].items()
            if value.get("status") == "complete"
        )
        failed = tuple(
            stage for stage, value in record["stages"].items()
            if value.get("status") == "failed"
        )
        errors = [
            str(value.get("error"))
            for value in record["stages"].values()
            if value.get("status") == "failed" and value.get("error")
        ]
        if record["complete"]:
            status = "success"
        elif completed:
            status = "partial"
        else:
            status = "failed"
        return DateResult(
            target_date,
            status,
            completed_stages=completed,
            failed_stages=failed,
            error="; ".join(errors) or None,
            output_path=record["stages"].get("daily", {}).get("path"),
        )

    def segment_result(
        self,
        exchange: str,
        segment: str,
        target_dates: Iterable[date],
    ) -> SegmentResult:
        dates = tuple(
            self.date_result(exchange, segment, target_date)
            for target_date in sorted(set(target_dates))
        )
        return SegmentResult(exchange.upper(), segment.upper(), dates)
