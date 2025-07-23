"""
Update Checker

Checks for application updates from GitHub repository.
"""

import requests
import json
import logging
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
    
    def __init__(self, current_version: str = "2.0.0", debug: bool = False):
        """
        Initialize update checker

        Args:
            current_version: Current application version
            debug: Enable debug mode (disables caching)
        """
        self.current_version = current_version
        self.debug = debug
        self.logger = logging.getLogger(__name__)
        
        # GitHub URLs
        self.github_base = "https://raw.githubusercontent.com/pparesh25/Getbhavcopy-alternative/main"
        self.update_info_url = f"{self.github_base}/update_info.json"
        self.download_url = "https://github.com/pparesh25/Getbhavcopy-alternative/archive/refs/heads/main.zip"

        self.logger.info(f"Update checker initialized:")
        self.logger.info(f"  Current version: {current_version}")
        self.logger.info(f"  Update info URL: {self.update_info_url}")
        self.logger.info(f"  Download URL: {self.download_url}")
        
        # Local cache
        self.cache_dir = Path.home() / ".nse_bse_downloader"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_file = self.cache_dir / "update_cache.json"
    
    def check_for_updates(self) -> Dict:
        """
        Check if updates are available
        
        Returns:
            Dictionary with update information
        """
        try:
            self.logger.info("Checking for updates...")
            
            # Fetch update info from GitHub
            response = requests.get(self.update_info_url, timeout=10)

            # Check for 404 or other errors
            if response.status_code == 404:
                self.logger.info("Update info file not found (404) - no updates available")
                return {"update_available": False, "error": "Update info not available"}

            response.raise_for_status()
            update_info = response.json()
            
            # Check if update is available
            latest_version = update_info.get("latest_version", "0.0.0")
            update_available = self._is_newer_version(latest_version, self.current_version)
            
            result = {
                "update_available": update_available,
                "current_version": self.current_version,
                "latest_version": latest_version,
                "update_info": update_info if update_available else None,
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
