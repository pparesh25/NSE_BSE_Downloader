"""
User Preferences Manager

Manages user preferences and settings persistence.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import date, datetime, timedelta


class UserPreferences:
    """
    Manages user preferences and settings persistence
    """

    def __init__(self):
        """Initialize user preferences manager"""
        self.logger = logging.getLogger(__name__)

        # User config directory
        self.config_dir = Path.home() / ".nse_bse_downloader"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Config file path
        self.config_file = self.config_dir / "user_preferences.json"

        # Default preferences
        self.default_preferences = {
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "exchange_selection": {
                "NSE_EQ": True,
                "NSE_FO": False,
                "NSE_SME": False,
                "NSE_INDEX": False,
                "BSE_EQ": False,
                "BSE_INDEX": False
            },
            "download_options": {
                "include_weekends": False,
                "timeout_seconds": 5,
                "include_delivery_data": True,
                "include_fo_open_interest": True,
                "generate_symbol_files": True,
                "apply_corporate_actions": True,
                "legacy_seven_column_output": False,
                # Append options
                "sme_add_suffix": False,
                "sme_append_to_eq": False,
                "index_append_to_eq": False,
                "bse_index_append_to_eq": False
            },
            "gui_settings": {
                "window_width": 576,
                "window_height": 850,
                "min_window_width": 550,
                "max_window_width": 650,
                "min_window_height": 750,
                "max_window_height": 1000,
                "last_download_location": str(Path.home() / "Downloads" / "NSE_BSE_Update"),
                "date_selection": {
                    "use_custom_range": False,
                    "start_date": (date.today() - timedelta(days=7)).isoformat(),
                    "end_date": date.today().isoformat()
                },
                "section_states": {
                    "exchanges": True,
                    "date_range": True,
                    "options": True,
                    "progress": False,
                    "status": True
                }
            },
            "advanced_options": {
                "auto_check_updates": True,
                "show_debug_logs": False,
                "cache_enabled": True
            }
        }

        # Load existing preferences
        self.preferences = self.load_preferences()

    def load_preferences(self) -> Dict[str, Any]:
        """
        Load user preferences from file

        Returns:
            Dictionary of user preferences
        """
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    saved_prefs = json.load(f)

                # Merge with defaults (in case new options were added)
                merged_prefs = self._merge_preferences(self.default_preferences, saved_prefs)

                self.logger.info(f"Loaded user preferences from: {self.config_file}")
                return merged_prefs
            else:
                self.logger.info("No existing preferences found, using defaults")
                return self.default_preferences.copy()

        except Exception as e:
            self.logger.error(f"Error loading preferences: {e}")
            self.logger.info("Using default preferences")
            return self.default_preferences.copy()

    def save_preferences(self) -> bool:
        """
        Save current preferences to file

        Returns:
            True if successful, False otherwise
        """
        try:
            # Update last_updated timestamp
            self.preferences["last_updated"] = datetime.now().isoformat()

            # Save to file with pretty formatting
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.preferences, f, indent=2, ensure_ascii=False)

            self.logger.info(f"Saved user preferences to: {self.config_file}")
            return True

        except Exception as e:
            self.logger.error(f"Error saving preferences: {e}")
            return False

    def _merge_preferences(self, defaults: Dict, saved: Dict) -> Dict:
        """
        Merge saved preferences with defaults (recursive)

        Args:
            defaults: Default preferences structure
            saved: Saved preferences

        Returns:
            Merged preferences dictionary
        """
        merged = defaults.copy()

        for key, value in saved.items():
            if key in merged:
                if isinstance(value, dict) and isinstance(merged[key], dict):
                    merged[key] = self._merge_preferences(merged[key], value)
                else:
                    merged[key] = value
            else:
                # New key from saved preferences
                merged[key] = value

        return merged

    # Exchange Selection Methods
    def get_selected_exchanges(self) -> List[str]:
        """Get list of selected exchanges"""
        exchange_prefs = self.preferences.get("exchange_selection", {})
        return [exchange for exchange, selected in exchange_prefs.items() if selected]

    def set_exchange_selection(self, exchanges: Dict[str, bool]) -> None:
        """Set exchange selection preferences"""
        self.preferences["exchange_selection"].update(exchanges)
        self.save_preferences()

    def is_exchange_selected(self, exchange: str) -> bool:
        """Check if specific exchange is selected"""
        return self.preferences.get("exchange_selection", {}).get(exchange, False)

    # Download Options Methods
    def get_download_options(self) -> Dict[str, Any]:
        """Get download options"""
        return self.preferences.get("download_options", {})

    def set_download_options(self, options: Dict[str, Any]) -> None:
        """Set download options"""
        self.preferences["download_options"].update(options)
        self.save_preferences()

    def get_include_weekends(self) -> bool:
        """Get include weekends setting"""
        return self.preferences.get("download_options", {}).get("include_weekends", False)

    def set_include_weekends(self, include: bool) -> None:
        """Set include weekends setting"""
        self.preferences["download_options"]["include_weekends"] = include
        self.save_preferences()

    def get_timeout_seconds(self) -> int:
        """Get timeout seconds setting"""
        return self.preferences.get("download_options", {}).get("timeout_seconds", 5)

    def set_timeout_seconds(self, timeout: int) -> None:
        """Set timeout seconds setting"""
        self.preferences["download_options"]["timeout_seconds"] = timeout
        self.save_preferences()

    def get_data_options(self) -> Dict[str, bool]:
        """Get canonical-output and symbol-history feature switches."""
        options = self.preferences.get("download_options", {})
        return {
            "include_delivery_data": options.get("include_delivery_data", True),
            "include_fo_open_interest": options.get("include_fo_open_interest", True),
            "generate_symbol_files": options.get("generate_symbol_files", True),
            "apply_corporate_actions": options.get("apply_corporate_actions", True),
            "legacy_seven_column_output": options.get(
                "legacy_seven_column_output", False
            ),
        }

    def set_data_options(self, options: Dict[str, bool]) -> None:
        """Persist canonical-output and symbol-history feature switches."""
        allowed = set(self.get_data_options())
        for key, value in options.items():
            if key in allowed:
                self.preferences["download_options"][key] = bool(value)
        self.save_preferences()

    # Append Options Methods
    def get_append_options(self) -> Dict[str, bool]:
        """Get all append options"""
        return {
            "sme_add_suffix": self.preferences.get("download_options", {}).get("sme_add_suffix", False),
            "sme_append_to_eq": self.preferences.get("download_options", {}).get("sme_append_to_eq", False),
            "index_append_to_eq": self.preferences.get("download_options", {}).get("index_append_to_eq", False),
            "bse_index_append_to_eq": self.preferences.get("download_options", {}).get("bse_index_append_to_eq", False)
        }

    def set_append_options(self, options: Dict[str, bool]) -> None:
        """Set append options"""
        for key, value in options.items():
            if key in ["sme_add_suffix", "sme_append_to_eq", "index_append_to_eq", "bse_index_append_to_eq"]:
                self.preferences["download_options"][key] = value
        self.save_preferences()

    def get_sme_add_suffix(self) -> bool:
        """Get SME add suffix setting"""
        return self.preferences.get("download_options", {}).get("sme_add_suffix", False)

    def set_sme_add_suffix(self, enabled: bool) -> None:
        """Set SME add suffix setting"""
        self.preferences["download_options"]["sme_add_suffix"] = enabled
        self.save_preferences()

    def get_sme_append_to_eq(self) -> bool:
        """Get SME append to EQ setting"""
        return self.preferences.get("download_options", {}).get("sme_append_to_eq", False)

    def set_sme_append_to_eq(self, enabled: bool) -> None:
        """Set SME append to EQ setting"""
        self.preferences["download_options"]["sme_append_to_eq"] = enabled
        self.save_preferences()

    def get_index_append_to_eq(self) -> bool:
        """Get Index append to EQ setting"""
        return self.preferences.get("download_options", {}).get("index_append_to_eq", False)

    def set_index_append_to_eq(self, enabled: bool) -> None:
        """Set Index append to EQ setting"""
        self.preferences["download_options"]["index_append_to_eq"] = enabled
        self.save_preferences()

    def get_bse_index_append_to_eq(self) -> bool:
        """Get BSE Index append to EQ setting"""
        return self.preferences.get("download_options", {}).get("bse_index_append_to_eq", False)

    def set_bse_index_append_to_eq(self, enabled: bool) -> None:
        """Set BSE Index append to EQ setting"""
        self.preferences["download_options"]["bse_index_append_to_eq"] = enabled
        self.save_preferences()

    # GUI Settings Methods
    def get_gui_settings(self) -> Dict[str, Any]:
        """Get GUI settings"""
        return self.preferences.get("gui_settings", {})

    def set_gui_settings(self, settings: Dict[str, Any]) -> None:
        """Set GUI settings"""
        self.preferences["gui_settings"].update(settings)
        self.save_preferences()

    def get_window_size(self) -> tuple[int, int]:
        """Get window size"""
        gui_settings = self.preferences.get("gui_settings", {})
        width = gui_settings.get("window_width", 800)
        height = gui_settings.get("window_height", 600)
        return width, height

    def set_window_size(self, width: int, height: int) -> None:
        """Set window size"""
        self.logger.debug(f"Saving window size: {width}x{height}")
        self.preferences["gui_settings"]["window_width"] = width
        self.preferences["gui_settings"]["window_height"] = height
        self.save_preferences()
        self.logger.debug(f"Window size saved successfully")

    def get_last_download_location(self) -> str:
        """Get last download location"""
        return self.preferences.get("gui_settings", {}).get(
            "last_download_location",
            str(Path.home() / "Downloads" / "NSE_BSE_Update")
        )

    def set_last_download_location(self, location: str) -> None:
        """Set last download location"""
        self.preferences["gui_settings"]["last_download_location"] = location
        self.save_preferences()

    def get_date_selection(self) -> Dict[str, Any]:
        """Return the saved automatic/custom date-range choice."""
        return self.preferences.get("gui_settings", {}).get(
            "date_selection", self.default_preferences["gui_settings"]["date_selection"]
        ).copy()

    def set_date_selection(
        self, use_custom_range: bool, start_date: date, end_date: date
    ) -> None:
        """Persist date-selection controls using ISO dates."""
        self.preferences["gui_settings"]["date_selection"] = {
            "use_custom_range": bool(use_custom_range),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        self.save_preferences()

    def get_section_states(self) -> Dict[str, bool]:
        """Return the expanded/collapsed state of every main-window section."""
        defaults = self.default_preferences["gui_settings"]["section_states"]
        saved = self.preferences.get("gui_settings", {}).get("section_states", {})
        return {key: bool(saved.get(key, value)) for key, value in defaults.items()}

    def set_section_state(self, section: str, expanded: bool) -> None:
        """Persist one disclosure section state."""
        states = self.preferences["gui_settings"].setdefault(
            "section_states", {}
        )
        states[section] = bool(expanded)
        self.save_preferences()

    # Advanced Options Methods
    def get_auto_check_updates(self) -> bool:
        """Get auto check updates setting"""
        return self.preferences.get("advanced_options", {}).get("auto_check_updates", True)

    def set_auto_check_updates(self, auto_check: bool) -> None:
        """Set auto check updates setting"""
        self.preferences["advanced_options"]["auto_check_updates"] = auto_check
        self.save_preferences()

    # Utility Methods
    def reset_to_defaults(self) -> None:
        """Reset all preferences to defaults"""
        self.preferences = self.default_preferences.copy()
        self.save_preferences()
        self.logger.info("Reset preferences to defaults")

    def export_preferences(self, file_path: Path) -> bool:
        """Export preferences to file"""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.preferences, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            self.logger.error(f"Error exporting preferences: {e}")
            return False

    def import_preferences(self, file_path: Path) -> bool:
        """Import preferences from file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                imported_prefs = json.load(f)

            self.preferences = self._merge_preferences(self.default_preferences, imported_prefs)
            self.save_preferences()
            return True
        except Exception as e:
            self.logger.error(f"Error importing preferences: {e}")
            return False

    def get_config_file_path(self) -> Path:
        """Get path to config file"""
        return self.config_file

    def get_config_directory(self) -> Path:
        """Get config directory path"""
        return self.config_dir
