"""Bounded retention for the diagnostic copies kept under ``.state``.

Three subtrees exist purely so a human can look at what went wrong after the
fact, and none of them is read by the application:

* ``quarantine`` -- bytes that failed validation, kept instead of being
  overwritten so a run can fail closed without destroying the evidence;
* ``raw_revisions`` -- the previous contents of a raw snapshot the exchange
  later republished with different bytes;
* ``backups/history`` -- a full copy of every symbol file, written before
  every symbol write until v1.1.0 removed it.

All three grow without limit, and the last one is written by nothing now, so
retention is what keeps a long backfill from leaving a permanent second copy
of the database behind.  ``.state/raw``, the checksummed rebuild source the
``--rebuild-*`` commands read, is deliberately not one of them.

Housekeeping must never be able to fail a run: every path here reports what it
could not do rather than raising, and the caller decides how loudly to say so.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class RetentionPolicy:
    """How much of one ``.state`` subtree to keep.

    ``group_depth`` names the directory level that identifies one logical
    bucket, counted from the subtree root and excluding the file name, so
    quarantine keeps files per *category* and raw revisions keep them per
    *date* rather than sharing one global budget.

    ``max_age_days`` of ``0`` removes everything -- which is how a tree
    nothing writes any more is drained -- and a negative value disables the
    age rule.  ``max_entries`` of ``None`` disables the count rule.  A policy
    with both rules disabled leaves its tree untouched.
    """

    directory: str
    group_depth: int
    max_age_days: int
    max_entries: Optional[int]

    @property
    def prunes_nothing(self) -> bool:
        return self.max_age_days < 0 and self.max_entries is None


@dataclass(frozen=True)
class RetentionOutcome:
    """What one policy actually did, in terms the caller can report."""

    directory: str
    removed_files: int
    removed_bytes: int
    kept_files: int
    errors: int


#: Shipped defaults.  Quarantine and raw revisions keep enough history to
#: diagnose a recent run; the legacy backup tree is drained because nothing
#: writes or reads it any more.
DEFAULT_POLICIES: tuple[RetentionPolicy, ...] = (
    RetentionPolicy("quarantine", 1, 30, 100),
    RetentionPolicy("raw_revisions", 3, 90, 5),
    RetentionPolicy("backups", 1, 0, None),
)


def policies_from_settings(settings: Any) -> tuple[RetentionPolicy, ...]:
    """Build policies from configuration, falling back per key.

    Accepts anything attribute-shaped, including ``None``, so a caller holding
    an older ``Config`` still gets the shipped defaults rather than an error.
    """

    def number(name: str, fallback: int) -> int:
        try:
            value = getattr(settings, name, None)
            return fallback if value is None else int(value)
        except (TypeError, ValueError):
            return fallback

    def entries(name: str, fallback: int) -> Optional[int]:
        value = number(name, fallback)
        return None if value < 0 else value

    return (
        RetentionPolicy(
            "quarantine",
            1,
            number("quarantine_days", 30),
            entries("quarantine_max_files", 100),
        ),
        RetentionPolicy(
            "raw_revisions",
            3,
            number("raw_revision_days", 90),
            entries("raw_revision_max_per_date", 5),
        ),
        RetentionPolicy(
            "backups",
            1,
            number("legacy_backup_days", 0),
            None,
        ),
    )


def _candidates(root: Path) -> list[tuple[Path, float, int]]:
    """Every regular file under ``root`` with its mtime and size.

    Symlinks are never followed and never returned: this code deletes what it
    finds, so a link pointing outside the state tree must not be reachable
    from here at all.
    """

    found: list[tuple[Path, float, int]] = []
    for directory, _sub_directories, file_names in os.walk(
        root, followlinks=False
    ):
        for name in file_names:
            path = Path(directory) / name
            try:
                if path.is_symlink():
                    continue
                stat = path.stat()
            except OSError:  # pragma: no cover - vanished mid-walk
                continue
            found.append((path, stat.st_mtime, stat.st_size))
    return found


def _group_key(path: Path, root: Path, depth: int) -> tuple[str, ...]:
    try:
        relative = path.relative_to(root)
    except ValueError:  # pragma: no cover - _candidates walks root only
        return ()
    return relative.parent.parts[:depth]


def _remove_empty_directories(root: Path) -> None:
    for directory, _sub_directories, _files in os.walk(
        root, topdown=False, followlinks=False
    ):
        candidate = Path(directory)
        try:
            candidate.rmdir()
        except OSError:
            # Not empty, or not ours to remove.  Either way the parent will
            # fail the same way, and neither is worth reporting.
            continue


def prune_state_tree(
    state_path: Path,
    policy: RetentionPolicy,
    *,
    now: Optional[float] = None,
) -> RetentionOutcome:
    """Apply one policy, removing the oldest files outside its budget."""

    root = Path(state_path) / policy.directory
    if policy.prunes_nothing or root.is_symlink() or not root.is_dir():
        return RetentionOutcome(policy.directory, 0, 0, 0, 0)

    moment = time.time() if now is None else now
    files = _candidates(root)

    doomed: list[tuple[Path, int]] = []
    survivors: dict[tuple[str, ...], list[tuple[float, str, Path, int]]] = {}
    for path, mtime, size in files:
        if policy.max_age_days >= 0 and (
            moment - mtime >= policy.max_age_days * 86400
        ):
            doomed.append((path, size))
            continue
        survivors.setdefault(
            _group_key(path, root, policy.group_depth), []
        ).append((mtime, path.name, path, size))

    kept = 0
    if policy.max_entries is None:
        kept = sum(len(group) for group in survivors.values())
    else:
        for group in survivors.values():
            # Newest first, name as the tie-break so two files written in the
            # same clock tick are still ordered the same way on every run.
            group.sort(key=lambda item: (-item[0], item[1]))
            kept += min(len(group), policy.max_entries)
            doomed.extend(
                (path, size)
                for _mtime, _name, path, size in group[policy.max_entries:]
            )

    removed_files = 0
    removed_bytes = 0
    errors = 0
    for path, size in doomed:
        try:
            path.unlink()
        except OSError:
            errors += 1
            continue
        removed_files += 1
        removed_bytes += size

    if removed_files:
        _remove_empty_directories(root)
    return RetentionOutcome(
        policy.directory, removed_files, removed_bytes, kept, errors
    )


def prune_state_directories(
    state_path: Path,
    policies: Sequence[RetentionPolicy] = DEFAULT_POLICIES,
    *,
    now: Optional[float] = None,
) -> tuple[RetentionOutcome, ...]:
    """Apply every policy, reporting each tree separately.

    One unreadable tree must not stop the others, so a policy that raises is
    reported as an error count rather than propagated.
    """

    state_path = Path(state_path)
    outcomes: list[RetentionOutcome] = []
    for policy in policies:
        try:
            outcomes.append(prune_state_tree(state_path, policy, now=now))
        except Exception:
            outcomes.append(RetentionOutcome(policy.directory, 0, 0, 0, 1))
    return tuple(outcomes)
