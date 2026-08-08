"""Validated, fail-closed and recoverable JSON state persistence."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Optional


class StateStoreError(RuntimeError):
    """Base class for persistent application state failures."""


class StateCorruptionError(StateStoreError):
    """Raised when existing state cannot be safely parsed or validated."""

    def __init__(
        self,
        path: Path,
        quarantine_path: Optional[Path],
        reason: Exception,
    ):
        self.path = Path(path)
        self.quarantine_path = quarantine_path
        self.reason = reason
        message = f"State file is corrupt and was not modified: {self.path}"
        if quarantine_path is not None:
            message += f" (backup: {quarantine_path})"
        message += f": {reason}"
        super().__init__(message)


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quarantine_copy(
    path: Path,
    quarantine_root: Path,
    category: str,
) -> Optional[Path]:
    """Copy damaged bytes to a deterministic quarantine path.

    The source is deliberately left untouched so callers can fail closed and a
    user can choose whether to restore, inspect or rebuild it.
    """

    path = Path(path)
    if not path.exists() or not path.is_file():
        return None
    try:
        digest = file_sha256(path)
        directory = Path(quarantine_root) / category
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{path.stem}.{digest[:12]}.corrupt"
        if not target.exists():
            shutil.copy2(path, target)
        return target
    except Exception:
        # The original parse/validation error remains the actionable failure.
        # A read-only disk may prevent the secondary quarantine copy.
        return None


def quarantine_move(
    path: Path,
    quarantine_root: Path,
    category: str,
) -> Optional[Path]:
    """Move a superseded file into quarantine instead of deleting it.

    Used for files whose content was folded into another file: the original
    must leave the published tree, but deleting it would make a wrong merge
    unrecoverable.  If the move fails the file is left in place -- a stale
    file is recoverable and visible, deleted bytes are neither.
    """

    path = Path(path)
    if not path.exists() or not path.is_file():
        return None
    try:
        digest = file_sha256(path)
        directory = Path(quarantine_root) / category
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{path.stem}.{digest[:12]}{path.suffix}"
        if target.exists():
            # Identical content is already preserved; finish the removal.
            path.unlink(missing_ok=True)
            return target
        try:
            path.replace(target)
        except OSError:
            shutil.move(str(path), str(target))
        return target
    except Exception:
        return None


class VersionedJSONStore:
    """Persist one JSON document using schema validation and atomic replace."""

    def __init__(
        self,
        path: Path,
        *,
        default: dict[str, Any],
        validator: Callable[[dict[str, Any]], None],
        quarantine_root: Optional[Path] = None,
        category: str = "state",
        migrate: Optional[
            Callable[[dict[str, Any]], dict[str, Any]]
        ] = None,
    ):
        self.path = Path(path)
        self.default = deepcopy(default)
        self.validator = validator
        self.quarantine_root = (
            Path(quarantine_root)
            if quarantine_root is not None
            else self.path.parent / "quarantine"
        )
        self.category = category
        self.migrate = migrate

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            result = deepcopy(self.default)
            self.validator(result)
            return result

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("top-level JSON value must be an object")
            if self.migrate is not None:
                payload = self.migrate(payload)
            self.validator(payload)
            return payload
        except StateCorruptionError:
            raise
        except Exception as error:
            quarantine_path = quarantine_copy(
                self.path, self.quarantine_root, self.category
            )
            raise StateCorruptionError(
                self.path, quarantine_path, error
            ) from error

    def write(self, payload: dict[str, Any]) -> None:
        self.validator(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        backup = self.path.with_name(self.path.name + ".bak")
        backup_temporary = backup.with_name(backup.name + ".tmp")

        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            if self.path.exists():
                shutil.copy2(self.path, backup_temporary)
                backup_temporary.replace(backup)
            temporary.replace(self.path)
            self._sync_parent_directory()
        finally:
            temporary.unlink(missing_ok=True)
            backup_temporary.unlink(missing_ok=True)

    def _sync_parent_directory(self) -> None:
        """Best-effort directory fsync so rename survives a sudden shutdown."""

        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
        except (AttributeError, OSError):
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)


CORPORATE_ACTION_STATUSES = (
    "applied",
    "prepared",
    "symbol_not_found",
    "no_prior_history",
    "awaiting_ex_date",
    "manual_review",
)


def corporate_action_key(
    exchange: str, stable_id: str, ex_date: str, action_type: str
) -> str:
    """Return the audit identity of one announcement.

    The parsed factor is deliberately absent.  It is a *reading* of the
    announcement, not part of what the announcement is, so including it would
    make every parser correction look like a new, unapplied action and divide
    an already-adjusted history a second time.
    """

    raw = "|".join([
        exchange.upper(), stable_id.upper(), str(ex_date), action_type.lower(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def default_corporate_ledger() -> dict[str, Any]:
    """Return a new v3 corporate-action ledger document."""

    return {"version": 3, "actions": {}, "transactions": {}}


def _rekey_corporate_actions(
    actions: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Re-derive every record's key without the factor, keeping the audit."""

    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    mapping: dict[str, str] = {}
    for old_key, record in actions.items():
        if not isinstance(record, dict):
            raise ValueError("invalid corporate-action ledger record")
        try:
            new_key = corporate_action_key(
                str(record["exchange"]),
                str(record["stable_id"]),
                str(record["ex_date"]),
                str(record["action_type"]),
            )
        except KeyError as error:
            raise ValueError(
                f"corporate-action record is missing {error}"
            ) from error
        mapping[old_key] = new_key
        grouped.setdefault(new_key, []).append((old_key, record))

    rekeyed: dict[str, Any] = {}
    for new_key, candidates in grouped.items():
        # Two v2 records can only collide here if the same announcement was
        # once read with two different factors.  Keep the one that reached the
        # history, so migration never demotes an applied adjustment.
        chosen = max(candidates, key=lambda item: (
            item[1].get("status") == "applied",
            str(item[1].get("updated_at", "")),
            item[0],
        ))[1]
        rekeyed[new_key] = deepcopy(chosen)
    return rekeyed, mapping


def migrate_corporate_ledger(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade older ledgers without losing audit records."""

    if data.get("version") == 1 and isinstance(data.get("actions"), dict):
        data = {
            "version": 2,
            "actions": deepcopy(data["actions"]),
            "transactions": {},
        }
    if data.get("version") != 2 or not isinstance(data.get("actions"), dict):
        return data

    transactions = data.get("transactions")
    if not isinstance(transactions, dict):
        return data
    actions, mapping = _rekey_corporate_actions(data["actions"])
    migrated_transactions: dict[str, Any] = {}
    for transaction_id, transaction in transactions.items():
        if not isinstance(transaction, dict):
            raise ValueError("invalid corporate-action transaction record")
        transaction = deepcopy(transaction)
        final_records = transaction.get("final_action_records")
        if isinstance(final_records, dict):
            rekeyed, _ = _rekey_corporate_actions(final_records)
            transaction["final_action_records"] = rekeyed
            transaction["action_keys"] = sorted(rekeyed)
        elif isinstance(transaction.get("action_keys"), list):
            transaction["action_keys"] = sorted({
                mapping.get(str(key), str(key))
                for key in transaction["action_keys"]
            })
        # The transaction id itself is left alone: a prepared transaction's
        # staged CSV is named after it, and renaming would orphan that file.
        migrated_transactions[transaction_id] = transaction
    return {
        "version": 3,
        "actions": actions,
        "transactions": migrated_transactions,
    }


def validate_corporate_ledger(data: dict[str, Any]) -> None:
    if data.get("version") != 3:
        raise ValueError("unsupported corporate-action ledger version")
    actions = data.get("actions")
    transactions = data.get("transactions")
    if not isinstance(actions, dict) or not isinstance(transactions, dict):
        raise ValueError("corporate-action ledger sections must be objects")
    for key, record in actions.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            raise ValueError("invalid corporate-action ledger record")
        if record.get("status") not in CORPORATE_ACTION_STATUSES:
            raise ValueError("corporate-action record has invalid status")
        for field in (
            "exchange", "symbol", "stable_id", "ex_date", "action_type"
        ):
            if not isinstance(record.get(field), str) or not record[field]:
                raise ValueError(
                    f"corporate-action record has invalid {field}"
                )
        factor = record.get("factor")
        rows_adjusted = record.get("rows_adjusted", 0)
        if not isinstance(factor, (int, float, str)) or not isinstance(
            rows_adjusted, (int, str)
        ):
            raise ValueError(
                "corporate-action record has invalid numeric fields"
            )
        try:
            if float(factor) <= 0:
                raise ValueError("factor must be positive")
            int(rows_adjusted)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "corporate-action record has invalid numeric fields"
            ) from error
    for key, record in transactions.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            raise ValueError("invalid corporate-action transaction record")
        if re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise ValueError("invalid corporate-action transaction id")
        if record.get("status") not in {"prepared", "committed"}:
            raise ValueError("corporate-action transaction has invalid status")
        for field in ("exchange", "symbol"):
            if not isinstance(record.get(field), str) or not record[field]:
                raise ValueError(
                    f"corporate-action transaction has invalid {field}"
                )
        for field in ("before_sha256", "after_sha256"):
            if re.fullmatch(r"[0-9a-f]{64}", str(record.get(field, ""))) is None:
                raise ValueError(
                    f"corporate-action transaction has invalid {field}"
                )
        action_keys = record.get("action_keys")
        final_records = record.get("final_action_records")
        if (
            not isinstance(action_keys, list)
            or not all(isinstance(value, str) for value in action_keys)
            or not isinstance(final_records, dict)
            or set(action_keys) != set(final_records)
        ):
            raise ValueError("corporate-action transaction action set mismatch")
