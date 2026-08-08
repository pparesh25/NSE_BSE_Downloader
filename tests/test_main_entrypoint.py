from argparse import Namespace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import main


def _args(**updates):
    values = {
        "rebuild_symbol": None,
        "rebuild_exchange": None,
        "rebuild_registry": False,
        "rebuild_all": False,
        "rebuild_combined": None,
    }
    values.update(updates)
    return Namespace(**values)


def test_argument_parser_exposes_gui_and_repair_modes():
    parser = main.setup_argument_parser()
    assert parser.parse_args([]).config.endswith("config.yaml")
    assert parser.parse_args(
        ["--rebuild-symbol", "NSE", "RELIANCE"]
    ).rebuild_symbol == ["NSE", "RELIANCE"]
    assert parser.parse_args(
        ["--rebuild-combined", "BSE", "2026-07-31"]
    ).rebuild_combined == ["BSE", "2026-07-31"]


def test_rebuild_service_modes_report_completed_work(
    tmp_path, monkeypatch, capsys
):
    # A real data root, not the working directory: the repair path now takes
    # the data-root lock, and ``Path("config.yaml").parent`` is the repo.
    monkeypatch.setattr(main, "Config", lambda _path: SimpleNamespace(
        base_data_path=tmp_path
    ))
    calls = []

    class Rebuilder:
        def __init__(self, base):
            calls.append(("init", base))

        def rebuild_symbol(self, exchange, symbol):
            calls.append(("symbol", exchange, symbol))
            return Path("RELIANCE.txt")

        def rebuild_exchange(self, exchange):
            calls.append(("exchange", exchange))
            return [Path("one.txt"), Path("two.txt")]

        def rebuild_registry(self):
            calls.append(("registry",))
            return 5

        def rebuild_all(self):
            calls.append(("all",))
            return {"NSE": [Path("one")], "BSE": [Path("two")]}

    monkeypatch.setattr(
        "src.services.rebuild_service.SymbolHistoryRebuilder", Rebuilder
    )
    assert main.run_rebuild_mode("config.yaml", _args(
        rebuild_symbol=["NSE", "RELIANCE"]
    )) == 0
    assert main.run_rebuild_mode("config.yaml", _args(rebuild_exchange="BSE")) == 0
    assert main.run_rebuild_mode("config.yaml", _args(rebuild_registry=True)) == 0
    assert main.run_rebuild_mode("config.yaml", _args(rebuild_all=True)) == 0
    output = capsys.readouterr().out
    assert "RELIANCE.txt" in output
    assert "Rebuilt 2 BSE symbol histories" in output
    assert "5 stable identifiers" in output
    assert "2 symbol histories across all exchanges" in output
    assert ("symbol", "NSE", "RELIANCE") in calls


def test_combined_rebuild_and_fail_closed_error(tmp_path, monkeypatch, capsys):
    config = SimpleNamespace(base_data_path=tmp_path)
    monkeypatch.setattr(main, "Config", lambda _path: config)

    class Builder:
        def __init__(self, received):
            assert received is config

        def dependencies_from_options(self, exchange, options):
            assert exchange == "NSE"
            assert options == {"index_append_to_eq": True}
            return ("INDEX",)

        def reconcile(self, exchange, target_date, dependencies):
            assert target_date == date.fromisoformat("2026-07-31")
            return SimpleNamespace(
                ok=True,
                output_path=Path("combined.txt"),
                rows=42,
                components=("EQ", *dependencies),
                error=None,
            )

    monkeypatch.setattr(
        "src.services.combined_file_builder.CombinedFileBuilder", Builder
    )
    monkeypatch.setattr(
        "src.utils.user_preferences.UserPreferences",
        lambda _config: SimpleNamespace(
            get_append_options=lambda: {"index_append_to_eq": True}
        ),
    )
    assert main.run_rebuild_mode(
        "config.yaml",
        _args(rebuild_combined=["NSE", "2026-07-31"]),
    ) == 0
    assert "42 rows" in capsys.readouterr().out

    monkeypatch.setattr(main, "Config", lambda _path: (_ for _ in ()).throw(
        RuntimeError("broken config")
    ))
    assert main.run_rebuild_mode("config.yaml", _args(rebuild_all=True)) == 1
    assert "existing data was left in place" in capsys.readouterr().out


def test_main_rejects_missing_config_and_routes_gui(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--config", str(missing)])
    assert main.main() == 1
    assert "not found" in capsys.readouterr().out

    config = tmp_path / "config.yaml"
    config.write_text("valid", encoding="utf-8")
    routes = []
    monkeypatch.setattr(main, "run_gui_mode", lambda path: routes.append(path) or 7)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--config", str(config)])
    assert main.main() == 7
    assert routes == [str(config)]


def test_gui_mode_reports_missing_qt(monkeypatch, capsys):
    monkeypatch.setattr(main, "GUI_AVAILABLE", False)
    assert main.run_gui_mode("config.yaml") == 1
    assert "PySide6 is not installed" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["--rebuild-all", "--rebuild-registry"])
def test_main_routes_repair_commands(tmp_path, monkeypatch, command):
    config = tmp_path / "config.yaml"
    config.write_text("valid", encoding="utf-8")
    received = []
    monkeypatch.setattr(
        main, "run_rebuild_mode", lambda path, args: received.append((path, args)) or 9
    )
    monkeypatch.setattr(
        main.sys, "argv", ["main.py", "--config", str(config), command]
    )
    assert main.main() == 9
    assert received[0][0] == str(config)
