import sys
from types import SimpleNamespace

import runtime_identity
from app_metadata import PRODUCT_NAME


def test_process_identity_uses_product_name(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "setproctitle",
        SimpleNamespace(setproctitle=calls.append),
    )
    monkeypatch.setattr(runtime_identity.sys, "platform", "linux")

    assert runtime_identity.configure_process_identity()
    assert calls == [PRODUCT_NAME]


def test_process_identity_is_best_effort(monkeypatch):
    monkeypatch.setitem(sys.modules, "setproctitle", None)
    monkeypatch.setattr(runtime_identity.sys, "platform", "linux")

    assert not runtime_identity.configure_process_identity()
