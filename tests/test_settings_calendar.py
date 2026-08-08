from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace

from src.core.data_manager import DataManager
from src.services.settings import SettingsService
from src.utils.date_utils import DateUtils
from src.utils import holiday_manager as holiday_module
from src.utils.holiday_manager import HolidayManager
from src.utils.user_preferences import MAX_TIMEOUT_SECONDS


class _SettingsConfig:
    download_settings = SimpleNamespace(timeout_seconds=400)
    gui_settings = SimpleNamespace(
        default_exchanges=["BSE_EQ"],
        window_width=620,
        window_height=900,
    )

    @staticmethod
    def get_download_options():
        return {
            "include_delivery_data": False,
            "index_append_to_eq": True,
        }


def test_settings_precedence_and_invalid_user_values_are_bounded(
    tmp_path, monkeypatch
):
    config = _SettingsConfig()
    fresh = SettingsService(config)
    assert fresh.get_download_option("include_delivery_data") is False
    assert fresh.get_download_option("index_append_to_eq") is True
    # 400 is above the supported ceiling and is clamped, not accepted.
    assert fresh.get_download_option("timeout_seconds") == MAX_TIMEOUT_SECONDS
    assert fresh.preferences.get_selected_exchanges() == ["BSE_EQ"]

    path = tmp_path / ".nse_bse_downloader" / "user_preferences.json"
    path.write_text(json.dumps({
        "download_options": {
            "timeout_seconds": -50,
            "include_delivery_data": "yes",
            "legacy_seven_column_output": True,
        },
        "unknown_section": {"unsafe": True},
    }), encoding="utf-8")
    saved = SettingsService(config).preferences
    assert saved.get_timeout_seconds() == 1
    assert saved.get_data_options()["include_delivery_data"] is True
    assert "legacy_seven_column_output" not in saved.get_data_options()
    assert "unknown_section" not in saved.preferences
    saved.save_preferences()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "legacy_seven_column_output" not in persisted["download_options"]


def test_legacy_narrow_window_preferences_migrate_to_responsive_layout(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / ".nse_bse_downloader"
    config_dir.mkdir()
    (config_dir / "user_preferences.json").write_text(json.dumps({
        "gui_settings": {
            "window_width": 648,
            "window_height": 910,
            "min_window_width": 550,
            "max_window_width": 650,
            "section_states": {"options": False},
        }
    }), encoding="utf-8")

    settings = SettingsService(_SettingsConfig()).preferences
    gui = settings.get_gui_settings()

    assert gui["layout_version"] == 3
    assert gui["window_width"] == 720
    assert gui["window_height"] == 910
    assert gui["min_window_width"] == 680
    assert gui["max_window_width"] == 1400
    assert gui["min_window_height"] == 420
    assert not settings.get_section_states()["options"]


def test_v1_0_1_user_preferences_upgrade_without_losing_user_choices(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / ".nse_bse_downloader"
    config_dir.mkdir()
    preference_path = config_dir / "user_preferences.json"
    preference_path.write_text(json.dumps({
        "version": "1.0",
        "exchange_selection": {
            "NSE_EQ": False,
            "NSE_FO": True,
            "NSE_SME": True,
            "NSE_INDEX": False,
            "BSE_EQ": True,
            "BSE_INDEX": False,
        },
        "download_options": {
            "include_weekends": True,
            "timeout_seconds": 17,
            "sme_add_suffix": True,
            "sme_append_to_eq": True,
            "index_append_to_eq": False,
            "bse_index_append_to_eq": True,
        },
        "gui_settings": {
            "window_width": 576,
            "window_height": 875,
            "min_window_width": 550,
            "max_window_width": 650,
            "min_window_height": 750,
            "max_window_height": 1000,
            "last_download_location": "/tmp/v1-update",
        },
        "advanced_options": {
            "auto_check_updates": False,
            "show_debug_logs": True,
            "cache_enabled": False,
        },
    }), encoding="utf-8")

    preferences = SettingsService(_SettingsConfig()).preferences

    assert preferences.get_selected_exchanges() == [
        "NSE_FO", "NSE_SME", "BSE_EQ"
    ]
    assert preferences.get_include_weekends() is True
    assert preferences.get_timeout_seconds() == 17
    assert preferences.get_append_options() == {
        "sme_add_suffix": True,
        "sme_append_to_eq": True,
        "index_append_to_eq": False,
        "bse_index_append_to_eq": True,
    }
    assert preferences.get_auto_check_updates() is False
    assert preferences.get_data_options() == {
        "include_delivery_data": False,
        "include_fo_open_interest": True,
        "generate_symbol_files": True,
        "apply_corporate_actions": True,
    }
    assert preferences.get_window_size() == (720, 875)
    assert preferences.get_gui_settings()["last_download_location"] == (
        "/tmp/v1-update"
    )


def test_date_utils_use_ist_and_injected_clock():
    before_release = datetime(2026, 7, 31, 12, 29, tzinfo=timezone.utc)
    after_release = datetime(2026, 7, 31, 12, 31, tzinfo=timezone.utc)
    assert not DateUtils.is_data_available_time(before_release)
    assert DateUtils.is_data_available_time(after_release)

    DateUtils.set_clock(
        lambda: datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    )
    try:
        assert DateUtils.today_ist() == date(2026, 8, 3)
        assert DateUtils.get_expected_last_trading_date(
            holidays={date(2026, 8, 3)}
        ) == date(2026, 7, 31)
    finally:
        DateUtils.reset_clock()


def test_holiday_cache_ttl_refresh_and_stale_fallback(tmp_path, monkeypatch):
    now = [datetime(2026, 7, 31, tzinfo=timezone.utc)]
    manager = HolidayManager(
        tmp_path,
        cache_ttl=timedelta(hours=24),
        now_provider=lambda: now[0],
        start_year=2026,
    )
    manager.save_holidays_to_cache({date(2026, 8, 15)})
    fetches = []
    monkeypatch.setattr(
        manager,
        "fetch_holidays_for_year",
        lambda year: fetches.append(year) or {date(2026, 10, 2)},
    )
    assert manager.get_holidays() == {date(2026, 8, 15)}
    assert fetches == []

    now[0] += timedelta(hours=25)
    assert manager.get_holidays() == {date(2026, 10, 2)}
    assert fetches == [2026]

    now[0] += timedelta(hours=25)
    failed_fetches = []
    monkeypatch.setattr(
        manager,
        "fetch_holidays_for_year",
        lambda year: failed_fetches.append(year) or None,
    )
    assert manager.get_holidays() == {date(2026, 10, 2)}
    assert manager.get_holidays() == {date(2026, 10, 2)}
    assert failed_fetches == [2026]


def test_official_nse_calendar_response_is_year_bounded(tmp_path, monkeypatch):
    requests = []

    def fake_fetch(url, timeout, *, headers):
        requests.append((url, timeout, headers))
        return json.dumps({
            "CM": [
                {"tradingDate": "26-Jan-2026"},
                {"tradingDate": "25-Dec-2026"},
                {"tradingDate": "01-Jan-2025"},
            ]
        })

    monkeypatch.setattr(holiday_module, "fetch_text_sync", fake_fetch)
    manager = HolidayManager(tmp_path, start_year=2026)
    assert manager.fetch_holidays_for_year(2026) == {
        date(2026, 1, 26),
        date(2026, 12, 25),
    }
    assert "year=2026" in requests[0][0]
    assert requests[0][2]["Referer"] == manager.SOURCE_PAGE


class _HolidayPolicyConfig:
    def __init__(self, base, skip):
        self.base_data_path = base
        self.date_settings = SimpleNamespace(
            weekend_skip=False,
            holiday_skip=skip,
            base_start_date="2026-01-01",
        )
        self.holiday_manager = SimpleNamespace(
            is_holiday=lambda value: True
        )

    @staticmethod
    def get_available_exchanges():
        return []


def test_holiday_skip_configuration_is_honored(tmp_path):
    day = date(2026, 8, 15)
    included = DataManager(_HolidayPolicyConfig(tmp_path / "on", False))
    excluded = DataManager(_HolidayPolicyConfig(tmp_path / "off", True))
    assert included.get_working_days(day, day) == [day]
    assert excluded.get_working_days(day, day) == []
