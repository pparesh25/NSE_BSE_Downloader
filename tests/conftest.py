"""Keep the test suite away from the developer's real market data.

`Config.__init__` resolves `~/NSE_BSE_Data` with `Path.expanduser()` and then
calls `mkdir(parents=True, exist_ok=True)` on it (`src/core/config.py:143,146`),
so merely constructing a `Config` reaches the real data root.  Several tests do
exactly that: `test_gui_lifecycle.py` and `test_packaging_readiness.py` build a
`Config` from the shipped `config.yaml`, and `test_main_entrypoint.py` drives
`run_rebuild_mode` with it.

Patching `Path.home` is not enough, and one test already showed why: it patched
`Path.home` and *then* constructed `Config("config.yaml")`.  `Path.expanduser()`
does not consult `Path.home` -- it reads the `HOME` environment variable through
`os.path.expanduser` -- so the real path was resolved anyway.  Redirecting the
environment variable covers both call styles at once.

Two guards back that up, because the cost of being wrong is someone's
accumulated market-data history:

* per test, a cheap stat of the real data root, so a violation names the test
  that caused it;
* once per session, a full fingerprint of that tree, which catches a write
  nested deeper than the root directory's own mtime reflects.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


#: Resolved at import time, before any fixture can redirect the environment.
REAL_HOME = Path(os.path.expanduser("~"))
REAL_DATA_ROOT = REAL_HOME / "NSE_BSE_Data"


def _root_state(root: Path) -> tuple[bool, int]:
    """Existence and mtime of ``root`` itself.  One stat call."""

    try:
        return True, root.stat().st_mtime_ns
    except OSError:
        return False, 0


def _tree_fingerprint(root: Path) -> tuple[tuple[str, int, int], ...] | None:
    """A value that changes if anything inside ``root`` is written.

    Stats only; never reads file contents.  Roughly 20 ms for a 10,000-entry
    tree, which is why it runs once per session rather than once per test.
    """

    if not root.exists():
        return None
    entries = []
    for path in sorted(root.rglob("*")):
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - vanished mid-walk
            continue
        entries.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


@pytest.fixture(autouse=True)
def isolate_home(tmp_path, monkeypatch):
    """Point every home-relative lookup at this test's temporary directory."""

    # tmp_path itself, not a subdirectory, so that `Path.home()` and
    # `Path('~').expanduser()` agree with each other and with the per-test
    # patches this fixture replaces.
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    # Both forms are asserted because the codebase uses both:
    # `Path(...).expanduser()` in config.py and `Path.home()` elsewhere.
    resolved_expanduser = Path("~").expanduser()
    assert resolved_expanduser == home, (
        "Path('~').expanduser() resolves outside the test directory: "
        f"{resolved_expanduser}"
    )
    resolved_home = Path.home()
    assert resolved_home == home, (
        f"Path.home() resolves outside the test directory: {resolved_home}"
    )

    return home


@pytest.fixture(autouse=True)
def guard_real_data_root(request):
    """Fail the individual test that reaches the developer's real data root."""

    before = _root_state(REAL_DATA_ROOT)

    yield

    after = _root_state(REAL_DATA_ROOT)
    if before == after:
        return
    if not before[0] and after[0]:
        pytest.fail(
            f"{request.node.nodeid} created the real data root {REAL_DATA_ROOT}. "
            "Use the isolate_home fixture's temporary home instead."
        )
    pytest.fail(
        f"{request.node.nodeid} modified the real data root {REAL_DATA_ROOT}. "
        "Tests must never write outside tmp_path."
    )


@pytest.fixture(scope="session", autouse=True)
def guard_real_data_tree():
    """Catch a deep write that the root directory's own mtime would not show."""

    before = _tree_fingerprint(REAL_DATA_ROOT)

    yield

    after = _tree_fingerprint(REAL_DATA_ROOT)
    if before != after:
        pytest.fail(
            f"The test session modified {REAL_DATA_ROOT}. Re-run with -p no:randomly "
            "to identify the test."
        )
