"""
Configuration Management and Profiles for CLI Mode

Provides advanced configuration management:
- View and update configuration settings
- Create and manage download profiles
- Profile switching and management
- Configuration validation
- Settings export/import
"""

import json
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

try:
    from colorama import Fore, Style
except ImportError:
    class Fore:
        RED = '\033[91m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        BLUE = '\033[94m'
        CYAN = '\033[96m'
        WHITE = '\033[97m'
        RESET = '\033[0m'
    
    class Style:
        BRIGHT = '\033[1m'
        RESET_ALL = '\033[0m'


@dataclass
class DownloadProfile:
    """Download profile configuration"""
    name: str
    description: str
    exchanges: List[str]
    timeout_seconds: int = 10
    retry_attempts: int = 3
    fast_mode: bool = False
    include_weekends: bool = False
    date_pattern: str = "last-7-days"
    created_at: str = ""
    last_used: str = ""
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class ConfigurationManager:
    """Advanced configuration management"""
    
    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.profiles_path = self.config_path.parent / "profiles.json"
        self.current_config = self._load_config()
        self.profiles = self._load_profiles()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load current configuration"""
        try:
            if self.config_path.suffix.lower() == '.yaml':
                with open(self.config_path, 'r') as f:
                    return yaml.safe_load(f) or {}
            else:
                with open(self.config_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"{Fore.YELLOW}⚠️  Warning: Could not load config: {e}{Style.RESET_ALL}")
            return {}
    
    def _save_config(self):
        """Save current configuration"""
        try:
            if self.config_path.suffix.lower() == '.yaml':
                with open(self.config_path, 'w') as f:
                    yaml.dump(self.current_config, f, default_flow_style=False, indent=2)
            else:
                with open(self.config_path, 'w') as f:
                    json.dump(self.current_config, f, indent=2)
            return True
        except Exception as e:
            print(f"{Fore.RED}❌ Error saving config: {e}{Style.RESET_ALL}")
            return False
    
    def _load_profiles(self) -> Dict[str, DownloadProfile]:
        """Load download profiles"""
        try:
            if self.profiles_path.exists():
                with open(self.profiles_path, 'r') as f:
                    profiles_data = json.load(f)
                    return {
                        name: DownloadProfile(**data) 
                        for name, data in profiles_data.items()
                    }
        except Exception as e:
            print(f"{Fore.YELLOW}⚠️  Warning: Could not load profiles: {e}{Style.RESET_ALL}")
        
        return {}
    
    def _save_profiles(self):
        """Save download profiles"""
        try:
            profiles_data = {
                name: asdict(profile) 
                for name, profile in self.profiles.items()
            }
            with open(self.profiles_path, 'w') as f:
                json.dump(profiles_data, f, indent=2)
            return True
        except Exception as e:
            print(f"{Fore.RED}❌ Error saving profiles: {e}{Style.RESET_ALL}")
            return False
    
    def display_current_config(self):
        """Display current configuration in a formatted way"""
        print(f"\n{Fore.CYAN}⚙️  Current Configuration{Style.RESET_ALL}")
        print("=" * 60)
        
        # Basic info
        print(f"{Fore.WHITE}📁 Config file: {self.config_path}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}📊 Profiles: {len(self.profiles)} available{Style.RESET_ALL}")
        
        # Configuration sections
        sections = {
            "Download Settings": ["timeout_seconds", "retry_attempts", "fast_mode"],
            "Data Paths": ["data_folder", "base_folder"],
            "Exchange Settings": ["exchanges", "default_exchanges"],
            "Logging": ["log_level", "log_file"]
        }
        
        for section_name, keys in sections.items():
            section_data = {}
            for key in keys:
                if key in self.current_config:
                    section_data[key] = self.current_config[key]
            
            if section_data:
                print(f"\n{Fore.YELLOW}{section_name}:{Style.RESET_ALL}")
                for key, value in section_data.items():
                    print(f"  {key}: {value}")
        
        # Show any other settings
        shown_keys = set()
        for keys in sections.values():
            shown_keys.update(keys)
        
        other_settings = {k: v for k, v in self.current_config.items() if k not in shown_keys}
        if other_settings:
            print(f"\n{Fore.YELLOW}Other Settings:{Style.RESET_ALL}")
            for key, value in other_settings.items():
                print(f"  {key}: {value}")
    
    def update_setting(self, key: str, value: Any) -> bool:
        """Update a configuration setting"""
        try:
            # Handle nested keys (e.g., "download.timeout")
            keys = key.split('.')
            current = self.current_config
            
            # Navigate to the parent of the target key
            for k in keys[:-1]:
                if k not in current:
                    current[k] = {}
                current = current[k]
            
            # Set the value
            current[keys[-1]] = value
            
            # Save the configuration
            if self._save_config():
                print(f"{Fore.GREEN}✅ Updated {key} = {value}{Style.RESET_ALL}")
                return True
            else:
                return False
                
        except Exception as e:
            print(f"{Fore.RED}❌ Error updating setting: {e}{Style.RESET_ALL}")
            return False
    
    def create_profile(self, name: str, description: str, exchanges: List[str], 
                      **kwargs) -> bool:
        """Create a new download profile"""
        if name in self.profiles:
            print(f"{Fore.RED}❌ Profile '{name}' already exists{Style.RESET_ALL}")
            return False
        
        try:
            profile = DownloadProfile(
                name=name,
                description=description,
                exchanges=exchanges,
                **kwargs
            )
            
            self.profiles[name] = profile
            
            if self._save_profiles():
                print(f"{Fore.GREEN}✅ Created profile '{name}'{Style.RESET_ALL}")
                return True
            else:
                return False
                
        except Exception as e:
            print(f"{Fore.RED}❌ Error creating profile: {e}{Style.RESET_ALL}")
            return False
    
    def delete_profile(self, name: str) -> bool:
        """Delete a download profile"""
        if name not in self.profiles:
            print(f"{Fore.RED}❌ Profile '{name}' not found{Style.RESET_ALL}")
            return False
        
        try:
            del self.profiles[name]
            
            if self._save_profiles():
                print(f"{Fore.GREEN}✅ Deleted profile '{name}'{Style.RESET_ALL}")
                return True
            else:
                return False
                
        except Exception as e:
            print(f"{Fore.RED}❌ Error deleting profile: {e}{Style.RESET_ALL}")
            return False
    
    def list_profiles(self):
        """Display all available profiles"""
        if not self.profiles:
            print(f"{Fore.YELLOW}No profiles found. Create one with 'create' command.{Style.RESET_ALL}")
            return
        
        print(f"\n{Fore.CYAN}📋 Available Profiles{Style.RESET_ALL}")
        print("=" * 60)
        
        for name, profile in self.profiles.items():
            print(f"\n{Fore.WHITE}{Style.BRIGHT}{name}{Style.RESET_ALL}")
            print(f"  Description: {profile.description}")
            print(f"  Exchanges: {', '.join(profile.exchanges)}")
            print(f"  Settings: timeout={profile.timeout_seconds}s, retries={profile.retry_attempts}")
            print(f"  Date pattern: {profile.date_pattern}")
            print(f"  Created: {profile.created_at[:10]}")
            if profile.last_used:
                print(f"  Last used: {profile.last_used[:10]}")
    
    def get_profile(self, name: str) -> Optional[DownloadProfile]:
        """Get a specific profile"""
        return self.profiles.get(name)
    
    def use_profile(self, name: str) -> Optional[DownloadProfile]:
        """Mark profile as used and return it"""
        if name in self.profiles:
            self.profiles[name].last_used = datetime.now().isoformat()
            self._save_profiles()
            return self.profiles[name]
        return None
    
    def export_config(self, export_path: Path) -> bool:
        """Export configuration and profiles"""
        try:
            export_data = {
                "config": self.current_config,
                "profiles": {name: asdict(profile) for name, profile in self.profiles.items()},
                "exported_at": datetime.now().isoformat(),
                "version": "2.0.0"
            }
            
            with open(export_path, 'w') as f:
                json.dump(export_data, f, indent=2)
            
            print(f"{Fore.GREEN}✅ Configuration exported to {export_path}{Style.RESET_ALL}")
            return True
            
        except Exception as e:
            print(f"{Fore.RED}❌ Error exporting config: {e}{Style.RESET_ALL}")
            return False
    
    def import_config(self, import_path: Path, merge: bool = True) -> bool:
        """Import configuration and profiles"""
        try:
            with open(import_path, 'r') as f:
                import_data = json.load(f)
            
            if merge:
                # Merge with existing config
                self.current_config.update(import_data.get("config", {}))
                
                # Merge profiles
                imported_profiles = import_data.get("profiles", {})
                for name, profile_data in imported_profiles.items():
                    self.profiles[name] = DownloadProfile(**profile_data)
            else:
                # Replace existing config
                self.current_config = import_data.get("config", {})
                self.profiles = {
                    name: DownloadProfile(**profile_data)
                    for name, profile_data in import_data.get("profiles", {}).items()
                }
            
            # Save changes
            success = self._save_config() and self._save_profiles()
            
            if success:
                action = "merged with" if merge else "replaced"
                print(f"{Fore.GREEN}✅ Configuration {action} imported data{Style.RESET_ALL}")
                return True
            else:
                return False
                
        except Exception as e:
            print(f"{Fore.RED}❌ Error importing config: {e}{Style.RESET_ALL}")
            return False
    
    def validate_config(self) -> List[str]:
        """Validate current configuration and return issues"""
        issues = []
        
        # Check required settings
        required_settings = ["timeout_seconds", "retry_attempts"]
        for setting in required_settings:
            if setting not in self.current_config:
                issues.append(f"Missing required setting: {setting}")
        
        # Validate data types and ranges
        if "timeout_seconds" in self.current_config:
            timeout = self.current_config["timeout_seconds"]
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                issues.append("timeout_seconds must be a positive number")
        
        if "retry_attempts" in self.current_config:
            retries = self.current_config["retry_attempts"]
            if not isinstance(retries, int) or retries < 0:
                issues.append("retry_attempts must be a non-negative integer")
        
        # Check data folder exists
        if "data_folder" in self.current_config:
            data_folder = Path(self.current_config["data_folder"])
            if not data_folder.exists():
                issues.append(f"Data folder does not exist: {data_folder}")
        
        return issues
