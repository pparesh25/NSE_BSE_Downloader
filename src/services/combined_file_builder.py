"""Deterministically assemble public EQ files from persisted components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from io import StringIO
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from ..core.exceptions import DataProcessingError, FileOperationError
from .canonical_data import EQUITY_DAILY_COLUMNS, INDEX_DAILY_COLUMNS
from .pipeline_state import PIPELINE_STAGES, PipelineManifest


class CombinedBuildError(RuntimeError):
    """Raised when required persisted components cannot be reconciled safely."""


@dataclass(frozen=True)
class ComponentSnapshot:
    exchange: str
    segment: str
    target_date: date
    path: Path
    sha256: str
    rows: int
    columns: tuple[str, ...]


@dataclass(frozen=True)
class CombinedBuildResult:
    exchange: str
    target_date: date
    status: str
    output_path: Optional[Path] = None
    components: tuple[str, ...] = ()
    rows: int = 0
    sha256: Optional[str] = None
    duplicate_keys: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status in {"success", "disabled"}


class CombinedFileBuilder:
    """Build byte-stable combined files independent of task arrival order."""

    COMPONENT_ORDER = {
        "NSE": ("EQ", "SME", "INDEX"),
        "BSE": ("EQ", "INDEX"),
    }
    OPTION_SEGMENTS = {
        "NSE": (
            ("sme_append_to_eq", "SME"),
            ("index_append_to_eq", "INDEX"),
        ),
        "BSE": (("bse_index_append_to_eq", "INDEX"),),
    }

    def __init__(self, config):
        self.config = config
        self.base_path = Path(config.base_data_path)
        self.component_root = self.base_path / ".state" / "components"
        self.pipeline = PipelineManifest(self.base_path)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def component_path(
        self, exchange: str, segment: str, target_date: date
    ) -> Path:
        return (
            self.component_root
            / exchange.upper()
            / segment.upper()
            / f"{target_date.isoformat()}.csv"
        )

    def save_component(
        self,
        exchange: str,
        segment: str,
        target_date: date,
        frame: pd.DataFrame,
    ) -> ComponentSnapshot:
        """Atomically persist a named-column input before public publication."""

        try:
            self._validate_component_frame(
                exchange, segment, target_date, frame
            )
        except CombinedBuildError as error:
            raise DataProcessingError(str(error)) from error
        columns = tuple(str(column) for column in frame.columns)

        path = self.component_path(exchange, segment, target_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            frame.to_csv(temporary, index=False)
            temporary.replace(path)
        except Exception as error:
            raise FileOperationError(
                f"Failed to persist {exchange}_{segment} component",
                file_path=str(path),
                operation="save_component",
            ) from error
        finally:
            temporary.unlink(missing_ok=True)
        return ComponentSnapshot(
            exchange.upper(),
            segment.upper(),
            target_date,
            path,
            self._sha256(path),
            len(frame),
            columns,
        )

    def load_component(
        self, exchange: str, segment: str, target_date: date
    ) -> pd.DataFrame:
        path = self.component_path(exchange, segment, target_date)
        if not path.is_file() or path.stat().st_size == 0:
            raise CombinedBuildError(
                f"Required component is unavailable: {exchange}_{segment} "
                f"for {target_date}"
            )
        try:
            # Preserve identifiers and their lexical representation (for
            # example a zero-prefixed security code) across restarts.
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        except Exception as error:
            raise CombinedBuildError(
                f"Unable to read component {path}: {error}"
            ) from error
        self._validate_component_frame(
            exchange,
            segment,
            target_date,
            frame,
            allow_seven_column_equity=True,
        )
        if (
            segment.upper() != "INDEX"
            and list(frame.columns) == EQUITY_DAILY_COLUMNS[:7]
        ):
            # Older staged checkpoints are accepted read-only and upgraded in
            # memory. New component writes must always use the stable extended
            # contract, and the source checkpoint is never rewritten here.
            frame = frame.reindex(columns=EQUITY_DAILY_COLUMNS, fill_value="")
        return frame

    @staticmethod
    def _text(values: pd.Series) -> pd.Series:
        """Normalize text without mutating pandas categorical dictionaries."""

        return values.astype("string").fillna("").str.strip()

    @staticmethod
    def _number(values: pd.Series) -> pd.Series:
        return pd.to_numeric(values.astype("object"), errors="coerce")

    @staticmethod
    def lexical_frame(frame: pd.DataFrame) -> pd.DataFrame:
        """Match the lexical representation produced by component reload."""

        stream = StringIO()
        frame.to_csv(stream, index=False)
        stream.seek(0)
        return pd.read_csv(stream, dtype=str, keep_default_na=False)

    @staticmethod
    def _validate_component_frame(
        exchange: str,
        segment: str,
        target_date: date,
        frame: pd.DataFrame,
        *,
        allow_seven_column_equity: bool = False,
    ) -> None:
        exchange = exchange.upper()
        segment = segment.upper()
        allowed_segments = CombinedFileBuilder.COMPONENT_ORDER.get(exchange)
        if allowed_segments is None or segment not in allowed_segments:
            raise CombinedBuildError(
                f"Unsupported combined component: {exchange}_{segment}"
            )
        if frame is None or frame.empty:
            raise CombinedBuildError(
                f"{exchange}_{segment} component has no rows"
            )
        columns = list(frame.columns)
        allowed = [INDEX_DAILY_COLUMNS] if segment == "INDEX" else [
            EQUITY_DAILY_COLUMNS
        ]
        if allow_seven_column_equity and segment != "INDEX":
            allowed.append(EQUITY_DAILY_COLUMNS[:7])
        if columns not in allowed:
            raise CombinedBuildError(
                f"Unexpected {exchange}_{segment} component columns: {columns}"
            )
        symbols = CombinedFileBuilder._text(frame["SYMBOL"])
        if symbols.eq("").any():
            raise CombinedBuildError(
                f"{exchange}_{segment} component contains a blank symbol"
            )
        expected_date = target_date.strftime("%Y%m%d")
        dates = (
            CombinedFileBuilder._text(frame["DATE"])
            .str.replace(r"\.0$", "", regex=True).str.strip()
        )
        if dates.ne(expected_date).any():
            raise CombinedBuildError(
                f"{exchange}_{segment} component contains a wrong date"
            )

        required_numeric = ["CLOSE"] if segment == "INDEX" else [
            "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"
        ]
        for column in required_numeric:
            values = CombinedFileBuilder._number(frame[column])
            if values.isna().any() or values.lt(0).any():
                raise CombinedBuildError(
                    f"{exchange}_{segment} component contains invalid {column}"
                )
        if segment == "INDEX":
            for column in ("OPEN", "HIGH", "LOW", "VOLUME"):
                text = CombinedFileBuilder._text(frame[column])
                values = CombinedFileBuilder._number(frame[column])
                invalid = text.ne("") & (values.isna() | values.lt(0))
                if invalid.any():
                    raise CombinedBuildError(
                        f"{exchange}_{segment} component contains invalid {column}"
                    )
        if columns == EQUITY_DAILY_COLUMNS:
            for column in ("DELIVERY_QTY", "DELIVERY_PERCENT"):
                text = CombinedFileBuilder._text(frame[column])
                values = CombinedFileBuilder._number(frame[column])
                invalid = text.ne("") & (values.isna() | values.lt(0))
                if invalid.any() or (
                    column == "DELIVERY_PERCENT"
                    and ((values < 0) | (values > 100)).fillna(False).any()
                ):
                    raise CombinedBuildError(
                        f"{exchange}_{segment} component contains invalid {column}"
                    )

    def component_exists(
        self, exchange: str, segment: str, target_date: date
    ) -> bool:
        try:
            self.load_component(exchange, segment, target_date)
            return True
        except CombinedBuildError:
            return False

    @classmethod
    def dependencies_from_options(
        cls,
        exchange: str,
        append_options: dict[str, bool],
        available_segments: Optional[Iterable[str]] = None,
    ) -> tuple[str, ...]:
        exchange = exchange.upper()
        available = (
            {value.upper() for value in available_segments}
            if available_segments is not None else None
        )
        result = []
        for option, segment in cls.OPTION_SEGMENTS.get(exchange, ()):
            full_name = f"{exchange}_{segment}"
            if append_options.get(option, False) and (
                available is None or full_name in available
            ):
                result.append(segment)
        return tuple(result)

    def _ensure_pipeline_date(self, exchange: str, target_date: date) -> None:
        if self.pipeline.has_date(exchange, "EQ", target_date):
            return
        self.pipeline.begin(
            exchange,
            "EQ",
            target_date,
            (),
            PIPELINE_STAGES,
        )

    def record_failure(
        self,
        exchange: str,
        target_date: date,
        dependencies: Iterable[str],
        error: str,
    ) -> CombinedBuildResult:
        exchange = exchange.upper()
        dependency_tuple = tuple(dependencies)
        self._ensure_pipeline_date(exchange, target_date)
        self.pipeline.require_stage(exchange, "EQ", target_date, "combined")
        self.pipeline.mark(
            exchange,
            "EQ",
            target_date,
            "combined",
            "failed",
            error=error,
            components=["EQ", *dependency_tuple],
        )
        return CombinedBuildResult(
            exchange,
            target_date,
            "failed",
            components=("EQ", *dependency_tuple),
            error=error,
        )

    def reconcile(
        self,
        exchange: str,
        target_date: date,
        dependencies: Iterable[str],
    ) -> CombinedBuildResult:
        """Atomically replace EQ only after every required component validates."""

        exchange = exchange.upper()
        if exchange not in self.COMPONENT_ORDER:
            raise ValueError(f"Unsupported combined exchange: {exchange}")
        allowed = self.COMPONENT_ORDER[exchange][1:]
        requested = tuple(dict.fromkeys(value.upper() for value in dependencies))
        invalid = set(requested).difference(allowed)
        if invalid:
            raise ValueError(
                f"Unsupported {exchange} combined components: {sorted(invalid)}"
            )
        ordered = tuple(value for value in allowed if value in requested)
        self._ensure_pipeline_date(exchange, target_date)

        try:
            frames = {"EQ": self.load_component(exchange, "EQ", target_date)}
            for segment in ordered:
                frames[segment] = self.load_component(
                    exchange, segment, target_date
                )
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, (CombinedBuildError, FileOperationError))
                else f"Combined reconciliation failed: {error}"
            )
            return self.record_failure(exchange, target_date, ordered, message)
        return self.reconcile_frames(
            exchange, target_date, ordered, frames
        )

    def reconcile_frames(
        self,
        exchange: str,
        target_date: date,
        dependencies: Iterable[str],
        frames: dict[str, pd.DataFrame],
    ) -> CombinedBuildResult:
        """Publish from prepared frames while retaining persisted fallback."""

        exchange = exchange.upper()
        if exchange not in self.COMPONENT_ORDER:
            raise ValueError(f"Unsupported combined exchange: {exchange}")
        allowed = self.COMPONENT_ORDER[exchange][1:]
        requested = tuple(dict.fromkeys(value.upper() for value in dependencies))
        invalid = set(requested).difference(allowed)
        if invalid:
            raise ValueError(
                f"Unsupported {exchange} combined components: {sorted(invalid)}"
            )
        ordered = tuple(value for value in allowed if value in requested)
        self._ensure_pipeline_date(exchange, target_date)

        try:
            required = ("EQ", *ordered)
            prepared = {}
            for segment in required:
                frame = frames.get(segment)
                if frame is None:
                    frame = self.load_component(exchange, segment, target_date)
                self._validate_component_frame(
                    exchange, segment, target_date, frame
                )
                prepared[segment] = frame

            frames = prepared
            base_columns = list(frames["EQ"].columns)
            if base_columns not in (
                EQUITY_DAILY_COLUMNS,
                EQUITY_DAILY_COLUMNS[:7],
            ):
                raise CombinedBuildError(
                    f"EQ component has unsupported columns: {base_columns}"
                )

            aligned = []
            for segment in ("EQ", *ordered):
                frame = frames[segment].copy()
                missing = [
                    column for column in INDEX_DAILY_COLUMNS
                    if column not in frame.columns
                ]
                if missing:
                    raise CombinedBuildError(
                        f"{exchange}_{segment} is missing columns: {missing}"
                    )
                frame = frame.reindex(columns=base_columns)
                aligned.append(frame)
            combined = pd.concat(aligned, ignore_index=True, sort=False)
            # Preserve official source rows in fixed source order, but record
            # every duplicate key including collisions between components.
            duplicate_keys = int(
                combined.duplicated(["SYMBOL", "DATE"]).sum()
            )

            output_path = (
                Path(self.config.get_data_path(exchange, "EQ"))
                / f"{target_date.isoformat()}-{exchange}-EQ.txt"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            try:
                combined.to_csv(temporary, index=False, header=False)
                digest = self._sha256(temporary)
                temporary.replace(output_path)
            finally:
                temporary.unlink(missing_ok=True)

            if ordered:
                self.pipeline.require_stage(
                    exchange, "EQ", target_date, "combined"
                )
                self.pipeline.mark(
                    exchange,
                    "EQ",
                    target_date,
                    "combined",
                    "complete",
                    path=str(output_path),
                    sha256=digest,
                    rows=len(combined),
                    components=["EQ", *ordered],
                    duplicate_keys=duplicate_keys,
                )
                status = "success"
            else:
                self.pipeline.disable_stage(
                    exchange, "EQ", target_date, "combined"
                )
                status = "disabled"
            return CombinedBuildResult(
                exchange,
                target_date,
                status,
                output_path=output_path,
                components=("EQ", *ordered),
                rows=len(combined),
                sha256=digest,
                duplicate_keys=duplicate_keys,
            )
        except Exception as error:
            if isinstance(error, (CombinedBuildError, FileOperationError)):
                message = str(error)
            else:
                message = f"Combined reconciliation failed: {error}"
            return self.record_failure(
                exchange, target_date, ordered, message
            )
