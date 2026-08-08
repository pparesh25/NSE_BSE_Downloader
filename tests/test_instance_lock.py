"""Phase 3.5: one writer per data root.

Two copies appending to one symbol history overwrite each other with no error
anywhere, so the second copy has to stop before it opens anything.
"""

from argparse import Namespace
import json
import os
from types import SimpleNamespace

import pytest

import main
from src.services.instance_lock import InstanceLockError, SingleInstanceLock


def test_a_second_copy_of_one_data_root_is_refused(tmp_path):
    first = SingleInstanceLock(tmp_path).acquire()
    try:
        with pytest.raises(InstanceLockError) as caught:
            SingleInstanceLock(tmp_path).acquire()
    finally:
        first.release()

    error = caught.value
    assert error.path == tmp_path / ".state" / "app.lock"
    assert error.holder is not None
    assert error.holder["pid"] == os.getpid()
    assert str(os.getpid()) in str(error)
    # Released, so the next copy is welcome.
    SingleInstanceLock(tmp_path).acquire().release()


def test_two_data_roots_do_not_contend(tmp_path):
    one = SingleInstanceLock(tmp_path / "one").acquire()
    two = SingleInstanceLock(tmp_path / "two").acquire()
    try:
        assert (tmp_path / "one" / ".state" / "app.lock").is_file()
        assert (tmp_path / "two" / ".state" / "app.lock").is_file()
    finally:
        one.release()
        two.release()


def test_the_holder_describes_itself_and_clears_the_note_on_release(tmp_path):
    lock = SingleInstanceLock(tmp_path).acquire()
    record = json.loads(lock.path.read_text())
    assert record["pid"] == os.getpid()
    assert record["host"] and record["started_at"]

    lock.release()
    assert lock.path.read_text() == ""


def test_acquiring_twice_from_one_owner_is_harmless(tmp_path):
    lock = SingleInstanceLock(tmp_path)
    assert lock.acquire() is lock.acquire()
    lock.release()
    lock.release()


def test_a_filesystem_without_locking_does_not_block_the_run(
    tmp_path, monkeypatch, caplog
):
    import src.services.instance_lock as instance_lock

    def unsupported(_handle):
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr(instance_lock, "_lock_handle", unsupported)
    with pytest.raises(InstanceLockError):
        SingleInstanceLock(tmp_path).acquire()

    monkeypatch.setattr(
        instance_lock, "_lock_handle", lambda _handle: False
    )
    # Reported as unavailable rather than held: the run continues, exactly as
    # it did before this guard existed.
    lock = SingleInstanceLock(tmp_path).acquire()
    lock.release()


def test_a_repair_command_refuses_to_run_beside_the_application(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        main, "Config", lambda _path: SimpleNamespace(base_data_path=tmp_path)
    )
    rebuilt = []

    class Rebuilder:
        def __init__(self, base):
            rebuilt.append(base)

        def rebuild_registry(self):
            return 5

    monkeypatch.setattr(
        "src.services.rebuild_service.SymbolHistoryRebuilder", Rebuilder
    )
    args = Namespace(
        rebuild_symbol=None,
        rebuild_exchange=None,
        rebuild_registry=True,
        rebuild_all=False,
        rebuild_combined=None,
    )

    held = SingleInstanceLock(tmp_path).acquire()
    try:
        assert main.run_rebuild_mode("config.yaml", args) == 1
    finally:
        held.release()
    assert "Repair not started" in capsys.readouterr().out
    assert rebuilt == []

    # With the application closed, the same repair runs and releases the lock.
    assert main.run_rebuild_mode("config.yaml", args) == 0
    assert rebuilt == [tmp_path]
    SingleInstanceLock(tmp_path).acquire().release()
