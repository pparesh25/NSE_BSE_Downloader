"""One writer per data root.

Two copies of the application pointed at the same tree destroy each other's
work silently.  Both read a symbol history, both append their own day, and the
one that writes second replaces the file the first just wrote; the registry and
the corporate-action ledger lose entries the same way.  Nothing in the run
reports it, because each process did exactly what it was asked.

The lock is an advisory whole-file lock held for the lifetime of the process
and keyed on the resolved data root -- the lock file lives inside it, so two
copies configured with different roots never contend, and two paths that reach
one root through a symlink share one inode and therefore one lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import logging
import os
from pathlib import Path
import socket
import sys
from typing import Any, IO, Optional


logger = logging.getLogger(__name__)

#: Errno values a non-blocking lock uses to say "someone else holds this".
#: Anything else means the filesystem cannot lock at all.
_HELD_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}


class InstanceLockError(RuntimeError):
    """Raised when another copy already owns this data root."""

    def __init__(self, path: Path, holder: Optional[dict[str, Any]]):
        self.path = Path(path)
        self.holder = holder
        described = ""
        if holder:
            pid = holder.get("pid")
            host = holder.get("host")
            started = holder.get("started_at")
            described = (
                f" (held by process {pid} on {host}, started {started})"
                if pid else ""
            )
        super().__init__(
            "Another copy of the application is already using this data "
            f"folder{described}. Close it and try again, or point this copy "
            f"at a different data folder. Lock file: {self.path}"
        )


@dataclass
class _LockState:
    handle: IO[str]
    locked: bool


def _lock_handle(handle: IO[str]) -> bool:
    """Take an exclusive non-blocking lock, or report that locking is absent.

    Returns ``True`` when the lock is held and ``False`` when the filesystem
    does not support locking -- a network share, typically.  Failing the run
    there would make the application unusable on a setup that works today, so
    that case degrades to the behaviour that existed before this guard.
    """

    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as error:
            if error.errno in _HELD_ERRNOS:
                raise
            logger.warning("Data-root locking is unavailable: %s", error)
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as error:
        if error.errno in _HELD_ERRNOS:
            raise
        logger.warning("Data-root locking is unavailable: %s", error)
        return False


def _unlock_handle(handle: IO[str]) -> None:
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:  # pragma: no cover - closing releases it anyway
        pass


class SingleInstanceLock:
    """Hold ``<base>/.state/app.lock`` for as long as this process runs."""

    def __init__(self, base_data_path: Path):
        self.base_path = Path(base_data_path)
        self.path = self.base_path / ".state" / "app.lock"
        self._state: Optional[_LockState] = None

    def _holder(self) -> Optional[dict[str, Any]]:
        """Read whatever the holding process wrote about itself."""

        try:
            content = self.path.read_text(encoding="utf-8").strip()
            record = json.loads(content) if content else None
            return record if isinstance(record, dict) else None
        except (OSError, ValueError):
            return None

    def acquire(self) -> "SingleInstanceLock":
        if self._state is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Opened for update rather than truncated: truncating first would erase
        # the running holder's own description before discovering it is there.
        handle = self.path.open("a+", encoding="utf-8")
        try:
            locked = _lock_handle(handle)
        except OSError:
            handle.close()
            raise InstanceLockError(self.path, self._holder()) from None
        except Exception:
            handle.close()
            raise
        if locked:
            try:
                handle.seek(0)
                handle.truncate()
                json.dump(
                    {
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "executable": sys.executable,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    },
                    handle,
                )
                handle.flush()
            except OSError as error:  # pragma: no cover - defensive
                # The lock itself is what matters; a description we could not
                # write only costs a less specific message to the next copy.
                logger.warning("Could not describe the lock holder: %s", error)
        self._state = _LockState(handle, locked)
        return self

    def release(self) -> None:
        state, self._state = self._state, None
        if state is None:
            return
        try:
            if state.locked:
                try:
                    state.handle.seek(0)
                    state.handle.truncate()
                    state.handle.flush()
                except OSError:  # pragma: no cover - defensive
                    pass
                _unlock_handle(state.handle)
        finally:
            state.handle.close()

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, *_exception: Any) -> None:
        self.release()
