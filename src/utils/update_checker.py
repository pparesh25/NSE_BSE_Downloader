"""
Update Checker

Checks for application updates from GitHub repository.
"""

import requests
import json
import logging
import sys
from typing import Dict, Optional, Tuple
from pathlib import Path
import zipfile
import shutil
import tempfile
from datetime import datetime


class UpdateChecker:
    """
    Checks for application updates from GitHub repository
    """

    def __init__(self, current_version: str = None, debug: bool = False):
        """
        Initialize update checker

        Args:
            current_version: Current application version (if None, will auto-detect from version.py)
            debug: Enable debug mode (disables caching)
        """
        self.debug = debug
        self.logger = logging.getLogger(__name__)

        # Auto-detect current version if not provided
        if current_version is None:
            current_version = self._get_local_version()

        self.current_version = current_version
        
        # GitHub URLs - Production repository
        self.github_base = "https://raw.githubusercontent.com/pparesh25/NSE_BSE_Downloader/main"
        self.version_info_url = f"{self.github_base}/version.py"
        self.download_url = "https://codeload.github.com/pparesh25/NSE_BSE_Downloader/zip/refs/heads/main"

        self.logger.info(f"Update checker initialized:")
        self.logger.info(f"  Current version: {current_version}")
        self.logger.info(f"  Version info URL: {self.version_info_url}")
        self.logger.info(f"  Download URL: {self.download_url}")
        
        # Local cache
        self.cache_dir = Path.home() / ".nse_bse_downloader"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_file = self.cache_dir / "update_cache.json"
    
    def check_for_updates(self) -> Dict:
        """
        Check if updates are available by comparing GitHub version.py

        Returns:
            Dictionary with update information
        """
        try:
            self.logger.info("🔍 DEBUG: Starting update check from GitHub version.py...")
            self.logger.info(f"🔍 DEBUG: Current local version: {self.current_version}")
            self.logger.info(f"🔍 DEBUG: GitHub URL: {self.version_info_url}")

            # Fetch version.py from GitHub
            self.logger.info("🔍 DEBUG: Fetching GitHub version.py...")
            response = requests.get(self.version_info_url, timeout=10)
            self.logger.info(f"🔍 DEBUG: GitHub response status: {response.status_code}")

            # Check for 404 or other errors
            if response.status_code == 404:
                self.logger.error("🔍 DEBUG: GitHub version.py file not found (404)")
                return {"update_available": False, "error": "GitHub version file not available"}

            response.raise_for_status()
            self.logger.info("🔍 DEBUG: Successfully fetched GitHub version.py")

            # Parse version.py content to extract version and changelog
            self.logger.info("🔍 DEBUG: Parsing GitHub version.py content...")
            github_version_info = self._parse_github_version_file(response.text)

            if not github_version_info:
                self.logger.error("🔍 DEBUG: Failed to parse GitHub version file")
                return {"update_available": False, "error": "Could not parse GitHub version file"}

            # Check if update is available
            latest_version = github_version_info.get("latest_version", "0.0.0")
            self.logger.info(f"🔍 DEBUG: Parsed GitHub version: {latest_version}")
            self.logger.info(f"🔍 DEBUG: Comparing {latest_version} vs {self.current_version}")

            update_available = self._is_newer_version(latest_version, self.current_version)
            self.logger.info(f"🔍 DEBUG: Update available result: {update_available}")

            result = {
                "update_available": update_available,
                "current_version": self.current_version,
                "latest_version": latest_version,
                "update_info": github_version_info if update_available else None,
                "error": None
            }

            # Cache the result (only if successful and not in debug mode)
            if not self.debug and result.get("update_available") is not None and not result.get("error"):
                self._cache_update_info(result)

            self.logger.info(f"Update check completed. Available: {update_available}")
            return result
            
        except requests.RequestException as e:
            self.logger.error(f"Network error checking for updates: {e}")
            # Don't use cache for network errors - return no update available
            return {"update_available": False, "error": f"Network error: {e}"}

        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing update info: {e}")
            return {"update_available": False, "error": "Invalid update data"}

        except Exception as e:
            self.logger.error(f"Unexpected error checking for updates: {e}")
            return {"update_available": False, "error": str(e)}
    
    def _is_newer_version(self, latest: str, current: str) -> bool:
        """
        Compare version strings to determine if update is available
        
        Args:
            latest: Latest version string (e.g., "2.1.0")
            current: Current version string (e.g., "2.0.0")
            
        Returns:
            True if latest version is newer
        """
        try:
            # Parse version strings
            latest_parts = [int(x) for x in latest.split('.')]
            current_parts = [int(x) for x in current.split('.')]
            
            # Pad shorter version with zeros
            max_len = max(len(latest_parts), len(current_parts))
            latest_parts.extend([0] * (max_len - len(latest_parts)))
            current_parts.extend([0] * (max_len - len(current_parts)))
            
            # Compare versions
            return latest_parts > current_parts
            
        except (ValueError, AttributeError):
            self.logger.warning(f"Could not parse versions: {latest} vs {current}")
            return False

    def _parse_github_version_file(self, content: str) -> Dict:
        """
        Parse GitHub version.py file content to extract version and changelog

        Args:
            content: Raw content of version.py file from GitHub

        Returns:
            Dictionary with version information and changelog
        """
        try:
            import re

            self.logger.info("🔍 DEBUG: Starting GitHub version.py parsing...")

            # Extract version
            version_match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if not version_match:
                self.logger.error("🔍 DEBUG: Could not find __version__ in GitHub version.py")
                self.logger.error(f"🔍 DEBUG: Content preview: {content[:200]}...")
                return {}

            version = version_match.group(1)
            self.logger.info(f"🔍 DEBUG: Extracted version: {version}")

            # Extract build date
            build_date_match = re.search(r'__build_date__\s*=\s*["\']([^"\']+)["\']', content)
            build_date = build_date_match.group(1) if build_date_match else "Unknown"

            # Extract VERSION_HISTORY
            version_history = {}
            try:
                # Find VERSION_HISTORY dictionary
                history_match = re.search(r'VERSION_HISTORY\s*=\s*({.*?})\s*(?=\n\w|\nclass|\ndef|\Z)', content, re.DOTALL)
                if history_match:
                    # Safely evaluate the dictionary (basic parsing)
                    history_str = history_match.group(1)
                    # Simple parsing for the latest version entry
                    latest_version_pattern = rf'"{re.escape(version)}"\s*:\s*{{([^}}]+)}}'
                    latest_match = re.search(latest_version_pattern, history_str, re.DOTALL)

                    if latest_match:
                        version_data = latest_match.group(1)

                        # Extract features
                        features_match = re.search(r'"features"\s*:\s*\[(.*?)\]', version_data, re.DOTALL)
                        features = []
                        if features_match:
                            features_str = features_match.group(1)
                            features = re.findall(r'"([^"]+)"', features_str)

                        # Extract bug fixes
                        bug_fixes_match = re.search(r'"bug_fixes"\s*:\s*\[(.*?)\]', version_data, re.DOTALL)
                        bug_fixes = []
                        if bug_fixes_match:
                            bug_fixes_str = bug_fixes_match.group(1)
                            bug_fixes = re.findall(r'"([^"]+)"', bug_fixes_str)

                        version_history = {
                            "version": version,
                            "release_date": build_date,
                            "features": features,
                            "bug_fixes": bug_fixes
                        }

            except Exception as e:
                self.logger.warning(f"Could not parse VERSION_HISTORY: {e}")

            # Create update info structure compatible with existing dialog
            update_info = {
                "latest_version": version,
                "update_available": True,
                "update_message": f"New version {version} available with improved features!",
                "release_date": build_date,
                "download_url": self.download_url,
                "changelog": version_history if version_history else {
                    "version": version,
                    "features": ["Updated to version " + version],
                    "bug_fixes": ["Various improvements and fixes"]
                }
            }

            self.logger.info(f"Parsed GitHub version: {version}")
            return update_info

        except Exception as e:
            self.logger.error(f"Error parsing GitHub version file: {e}")
            return {}
    
    def download_update(self, download_path: Optional[Path] = None) -> Tuple[bool, str]:
        """
        Download update ZIP file
        
        Args:
            download_path: Path to save the downloaded file
            
        Returns:
            Tuple of (success, message/error)
        """
        try:
            if download_path is None:
                download_path = self.cache_dir / f"update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            
            self.logger.info(f"Downloading update from: {self.download_url}")
            
            # Download with progress
            response = requests.get(self.download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            
            with open(download_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        
                        # Log progress every 1MB
                        if downloaded_size % (1024 * 1024) == 0:
                            if total_size > 0:
                                progress = (downloaded_size / total_size) * 100
                                self.logger.info(f"Download progress: {progress:.1f}%")
            
            self.logger.info(f"Update downloaded successfully: {download_path}")
            return True, str(download_path)
            
        except requests.RequestException as e:
            error_msg = f"Network error downloading update: {e}"
            self.logger.error(error_msg)
            return False, error_msg
            
        except Exception as e:
            error_msg = f"Error downloading update: {e}"
            self.logger.error(error_msg)
            return False, error_msg
    
    def extract_update(self, zip_path: Path, extract_to: Optional[Path] = None) -> Tuple[bool, str]:
        """
        Extract downloaded update ZIP file
        
        Args:
            zip_path: Path to the ZIP file
            extract_to: Directory to extract to
            
        Returns:
            Tuple of (success, message/error)
        """
        try:
            if extract_to is None:
                extract_to = self.cache_dir / "extracted_update"
            
            # Remove existing extraction directory
            if extract_to.exists():
                shutil.rmtree(extract_to)
            
            extract_to.mkdir(parents=True, exist_ok=True)
            
            self.logger.info(f"Extracting update to: {extract_to}")
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_to)
            
            # Find the extracted folder (usually has a name like "repo-main")
            extracted_folders = [d for d in extract_to.iterdir() if d.is_dir()]
            if extracted_folders:
                actual_folder = extracted_folders[0]
                self.logger.info(f"Update extracted successfully: {actual_folder}")
                return True, str(actual_folder)
            else:
                error_msg = "No folders found in extracted update"
                self.logger.error(error_msg)
                return False, error_msg
                
        except zipfile.BadZipFile as e:
            error_msg = f"Invalid ZIP file: {e}"
            self.logger.error(error_msg)
            return False, error_msg
            
        except Exception as e:
            error_msg = f"Error extracting update: {e}"
            self.logger.error(error_msg)
            return False, error_msg
    
    def _cache_update_info(self, update_info: Dict) -> None:
        """Cache update information locally"""
        try:
            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "update_info": update_info
            }
            
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
                
        except Exception as e:
            self.logger.warning(f"Could not cache update info: {e}")
    
    def _get_cached_update_info(self) -> Dict:
        """Get cached update information if available"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r') as f:
                    cache_data = json.load(f)
                
                # Check if cache is recent (within 24 hours)
                cache_time = datetime.fromisoformat(cache_data["timestamp"])
                if (datetime.now() - cache_time).total_seconds() < 86400:  # 24 hours
                    self.logger.info("Using cached update info")
                    return cache_data["update_info"]
            
        except Exception as e:
            self.logger.warning(f"Could not load cached update info: {e}")
        
        return {"update_available": False, "error": "No cached data available"}
    
    def get_current_version(self) -> str:
        """Get current application version"""
        return self.current_version
    
    def set_current_version(self, version: str) -> None:
        """Set current application version"""
        self.current_version = version

    def _get_local_version(self) -> str:
        """
        Get current version from local version.py file

        Returns:
            Version string from version.py, defaults to "0.0.0" if not found
        """
        try:
            # Try to find version.py in multiple possible locations
            possible_paths = [
                # From src/utils/ directory, go up to project root
                Path(__file__).parent.parent.parent / "version.py",
                # From current working directory
                Path.cwd() / "version.py",
                # From sys.path[0] (script directory)
                Path(sys.path[0]) / "version.py" if sys.path else None
            ]

            # Filter out None paths
            possible_paths = [p for p in possible_paths if p is not None]

            for version_path in possible_paths:
                if version_path.exists():
                    self.logger.info(f"Found version.py at: {version_path}")

                    # Read and parse version.py
                    version_content = version_path.read_text(encoding='utf-8')

                    # Extract __version__ using simple parsing
                    for line in version_content.split('\n'):
                        line = line.strip()
                        if line.startswith('__version__') and '=' in line:
                            # Extract version string
                            version_part = line.split('=', 1)[1].strip()
                            # Remove quotes
                            version = version_part.strip('"\'')
                            self.logger.info(f"Detected local version: {version}")
                            return version

                    self.logger.warning(f"Could not find __version__ in {version_path}")

            self.logger.warning("Could not find version.py file in any expected location")
            return "0.0.0"

        except Exception as e:
            self.logger.error(f"Error reading local version: {e}")
            return "0.0.0"
