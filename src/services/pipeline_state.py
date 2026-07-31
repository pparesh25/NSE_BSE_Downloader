"""Crash-safe per-date pipeline state and structured download results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Optional

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
        self._state = VersionedJSONStore(
            self.path,
            default={"version": 1, "dates": {}},
            validator=self._validate,
            quarantine_root=state_dir / "quarantine",
            category="pipeline_manifest",
        )

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
            if not all(isinstance(value, str) and value for value in (
                exchange, segment, raw_date
            )):
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
        with self._lock:
            data = self._state.read()
            record = data["dates"].setdefault(key, {
                "exchange": exchange.upper(),
                "segment": segment.upper(),
                "date": target_date.isoformat(),
                "required_stages": required,
                "stages": {},
                "complete": False,
                "updated_at": self._timestamp(),
            })
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
            self._state.write(data)

    def mark(
        self,
        exchange: str,
        segment: str,
        target_date: date,
        stage: str,
        status: str,
        **metadata: Any,
    ) -> None:
        if stage not in PIPELINE_STAGES or status not in STAGE_STATUSES:
            raise ValueError(f"Invalid pipeline stage update: {stage}={status}")
        key = self._key(exchange, segment, target_date)
        with self._lock:
            data = self._state.read()
            if key not in data["dates"]:
                raise ValueError(f"Pipeline date was not started: {key}")
            stage_record = {
                "status": status,
                "updated_at": self._timestamp(),
            }
            for name, value in metadata.items():
                if value is not None:
                    stage_record[name] = value
            data["dates"][key]["stages"][stage] = stage_record
            self._refresh(data["dates"][key])
            self._state.write(data)

    def has_date(
        self, exchange: str, segment: str, target_date: date
    ) -> bool:
        key = self._key(exchange, segment, target_date)
        with self._lock:
            return key in self._state.read()["dates"]

    def require_stage(
        self, exchange: str, segment: str, target_date: date, stage: str
    ) -> None:
        """Make a stage part of completion and reset it for reconciliation."""

        if stage not in PIPELINE_STAGES:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        key = self._key(exchange, segment, target_date)
        with self._lock:
            data = self._state.read()
            if key not in data["dates"]:
                raise ValueError(f"Pipeline date was not started: {key}")
            record = data["dates"][key]
            if stage not in record["required_stages"]:
                record["required_stages"].append(stage)
            record["stages"][stage] = {
                "status": "pending",
                "updated_at": self._timestamp(),
            }
            self._refresh(record)
            self._state.write(data)

    def disable_stage(
        self, exchange: str, segment: str, target_date: date, stage: str
    ) -> None:
        """Remove a disabled optional stage from the completion requirement."""

        if stage not in PIPELINE_STAGES:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        key = self._key(exchange, segment, target_date)
        with self._lock:
            data = self._state.read()
            if key not in data["dates"]:
                raise ValueError(f"Pipeline date was not started: {key}")
            record = data["dates"][key]
            record["required_stages"] = [
                value for value in record["required_stages"] if value != stage
            ]
            record["stages"][stage] = {
                "status": "disabled",
                "updated_at": self._timestamp(),
            }
            self._refresh(record)
            self._state.write(data)

    def skip_date(
        self, exchange: str, segment: str, target_date: date, reason: str
    ) -> None:
        self.begin(exchange, segment, target_date, ())
        key = self._key(exchange, segment, target_date)
        with self._lock:
            data = self._state.read()
            data["dates"][key]["skipped_reason"] = reason
            data["dates"][key]["complete"] = True
            data["dates"][key]["updated_at"] = self._timestamp()
            self._state.write(data)

    def incomplete_dates(self, exchange: str, segment: str) -> list[date]:
        prefix = f"{exchange.upper()}_{segment.upper()}:"
        with self._lock:
            records = self._state.read()["dates"]
        return sorted(
            date.fromisoformat(record["date"])
            for key, record in records.items()
            if key.startswith(prefix)
            and not record["complete"]
            and not record.get("skipped_reason")
        )

    def date_result(
        self, exchange: str, segment: str, target_date: date
    ) -> DateResult:
        key = self._key(exchange, segment, target_date)
        with self._lock:
            record = self._state.read()["dates"].get(key)
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
