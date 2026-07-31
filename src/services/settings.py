"""Validated effective settings with explicit precedence."""

from __future__ import annotations

from typing import Any, Optional

from ..utils.user_preferences import UserPreferences


class SettingsService:
    """Resolve built-ins, config defaults, then persisted user overrides."""

    def __init__(self, config, preferences: Optional[UserPreferences] = None):
        self.config = config
        self.preferences = preferences or UserPreferences(config)

    def download_options(self) -> dict[str, Any]:
        return self.preferences.get_download_options()

    def get_download_option(self, name: str, default: Any = None) -> Any:
        return self.download_options().get(name, default)

    def append_options(self) -> dict[str, bool]:
        return self.preferences.get_append_options()

    def auto_check_updates(self) -> bool:
        return self.preferences.get_auto_check_updates()

    def skipped_update_version(self) -> str:
        return self.preferences.get_skipped_update_version()
