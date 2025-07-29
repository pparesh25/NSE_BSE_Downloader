"""
User Preferences Manager

Manages user preferences and settings persistence.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime


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
                # Append options
                "sme_add_suffix": False,
                "sme_append_to_eq": False,
                "index_append_to_eq": False,
                "bse_index_append_to_eq": False
            },
            "gui_settings": {
                "window_width": 800,
                "window_height": 600,
                "last_download_location": str(Path.home() / "Downloads" / "NSE_BSE_Update")
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
