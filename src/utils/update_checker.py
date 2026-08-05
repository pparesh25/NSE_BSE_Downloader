"""
Update Checker

Checks for application updates from GitHub repository.
"""

import ast
import json
import hashlib
import logging
import platform
import re
import stat
import sys
from typing import Dict, Optional, Tuple
from pathlib import Path, PurePosixPath
import zipfile
import shutil
from datetime import datetime
from urllib.parse import urlparse

from .http_client import fetch_text_sync, download_to_file_sync, HTTPStatusError


def release_platform_key(
    system: Optional[str] = None, machine: Optional[str] = None
) -> str:
    """Return the ``<platform>-<architecture>`` key for this machine.

    The names match what ``package_release_artifact.py`` puts in each release
    archive name, so a published asset and the running application agree on one
    spelling.
    """

    name = (system or platform.system()).strip().lower()
    systems = {"darwin": "darwin", "windows": "windows", "linux": "linux"}
    architecture = (machine or platform.machine()).strip().lower()
    architectures = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    return (
        f"{systems.get(name, name)}-"
        f"{architectures.get(architecture, architecture.replace(' ', '-'))}"
    )


class UpdateChecker:
    """
    Checks for application updates from GitHub repository
    """

    MAX_UPDATE_BYTES = 500 * 1024 * 1024
    MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
    MAX_ARCHIVE_MEMBERS = 20_000
    GITHUB_REPOSITORY = "pparesh25/NSE_BSE_Downloader"

    def __init__(self, current_version: Optional[str] = None, debug: bool = False):
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
        self.github_base = (
            f"https://raw.githubusercontent.com/{self.GITHUB_REPOSITORY}/main"
        )
        self.version_info_url = f"{self.github_base}/version.py"
        # The metadata endpoint may follow main so it can announce a release,
        # but executable code is accepted only from an immutable release/tag
        # URL with a matching SHA-256 digest.
        self.download_url: Optional[str] = None
        self.expected_sha256: Optional[str] = None

        self.logger.info("Update checker initialized:")
        self.logger.info(f"  Current version: {current_version}")
        self.logger.info(f"  Version info URL: {self.version_info_url}")
        self.logger.info("  Update artifact: waiting for verified release metadata")

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
            try:
                text = fetch_text_sync(self.version_info_url, timeout=10)
                status_code = 200
            except HTTPStatusError as he:
                status_code = he.status
                if he.status == 404:
                    self.logger.error("🔍 DEBUG: GitHub version.py file not found (404)")
                    return {"update_available": False, "error": "GitHub version file not available"}
                raise

            self.logger.info(f"🔍 DEBUG: GitHub response status: {status_code}")
            self.logger.info("🔍 DEBUG: Successfully fetched GitHub version.py")

            # Parse version.py content to extract version and changelog
            self.logger.info("🔍 DEBUG: Parsing GitHub version.py content...")
            github_version_info = self._parse_github_version_file(text)

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

        except HTTPStatusError as e:
            self.logger.error(f"Network error checking for updates: {e}")
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

    def _artifact_for_this_platform(self, content: str) -> Tuple[str, str]:
        """Return the ``(url, sha256)`` published for the running platform.

        Returns a pair of empty strings whenever the mapping is absent,
        unparseable, or has no entry for this platform, which keeps the caller
        on the notification-only path.  The published file is remote data, so it
        is read with ``ast.literal_eval`` and never executed.
        """

        match = re.search(
            r"^__update_artifacts__[^=]*=\s*(\{.*?^\})",
            content,
            re.DOTALL | re.MULTILINE,
        )
        if not match:
            return "", ""

        try:
            artifacts = ast.literal_eval(match.group(1))
        except (ValueError, SyntaxError, MemoryError, RecursionError) as error:
            self.logger.warning(f"Could not parse __update_artifacts__: {error}")
            return "", ""

        if not isinstance(artifacts, dict):
            self.logger.warning("__update_artifacts__ is not a mapping")
            return "", ""

        key = release_platform_key()
        entry = artifacts.get(key)
        if entry is None:
            self.logger.info(
                f"No published update artifact for this platform ({key})"
            )
            return "", ""
        if not isinstance(entry, dict):
            self.logger.warning(f"__update_artifacts__[{key!r}] is not a mapping")
            return "", ""

        url = entry.get("url")
        digest = entry.get("sha256")
        if not isinstance(url, str) or not isinstance(digest, str):
            self.logger.warning(
                f"__update_artifacts__[{key!r}] is missing a string url/sha256"
            )
            return "", ""
        return url.strip(), digest.strip()

    def _parse_github_version_file(self, content: str) -> Dict:
        """
        Parse GitHub version.py file content to extract version and changelog

        Args:
            content: Raw content of version.py file from GitHub

        Returns:
            Dictionary with version information and changelog
        """
        try:
            self.logger.info("🔍 DEBUG: Starting GitHub version.py parsing...")

            # Never let metadata from an earlier update check authorize a later
            # update whose package metadata is absent or invalid.
            self.download_url = None
            self.expected_sha256 = None

            # Extract version
            version_match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if not version_match:
                self.logger.error("🔍 DEBUG: Could not find __version__ in GitHub version.py")
                self.logger.error(f"🔍 DEBUG: Content preview: {content[:200]}...")
                return {}

            version = version_match.group(1)
            self.logger.info(f"🔍 DEBUG: Extracted version: {version}")

            update_url, update_sha256 = self._artifact_for_this_platform(content)
            artifact_verified = False
            artifact_error = "Verified update package metadata is unavailable"
            if update_url and update_sha256:
                artifact_verified, artifact_error = (
                    self.configure_update_artifact(
                        update_url, update_sha256, version
                    )
                )

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
                "download_url": update_url or None,
                "sha256": update_sha256 or None,
                "artifact_verified": artifact_verified,
                "artifact_error": None if artifact_verified else artifact_error,
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

    def configure_update_artifact(
        self,
        download_url: str,
        expected_sha256: str,
        expected_version: str,
    ) -> Tuple[bool, str]:
        """Validate and retain immutable, checksum-bound update metadata."""

        # Fail closed. A failed reconfiguration must not preserve an older,
        # otherwise valid artifact for a different version.
        self.download_url = None
        self.expected_sha256 = None

        parsed = urlparse(str(download_url).strip())
        if parsed.scheme != "https" or not parsed.netloc:
            return False, "Update URL must use HTTPS"

        hostname = (parsed.hostname or "").lower()
        # A dot segment would let "/<repo>/releases/download/v1/../../elsewhere"
        # satisfy the prefix test below while the server resolves it somewhere
        # else entirely.  Reject rather than normalize: a released asset URL
        # never contains one.
        if any(part in {".", ".."} for part in parsed.path.split("/")):
            return False, "Update URL must not contain relative path segments"

        path = parsed.path.lower()
        repository_path = f"/{self.GITHUB_REPOSITORY.lower()}"
        github_release = (
            hostname == "github.com"
            and path.startswith(f"{repository_path}/releases/download/")
        )
        codeload_tag = (
            hostname == "codeload.github.com"
            and path.startswith(f"{repository_path}/zip/refs/tags/")
        )
        if not github_release and not codeload_tag:
            return (
                False,
                "Update URL must reference an immutable release or tag from "
                f"{self.GITHUB_REPOSITORY}",
            )

        path_parts = [part for part in parsed.path.split("/") if part]
        tag_index = 4 if github_release else 5
        if len(path_parts) <= tag_index:
            return False, "Update URL is missing its release tag"
        release_tag = path_parts[tag_index]
        normalized_tag = release_tag.lower().removeprefix("v")
        normalized_version = str(expected_version).strip().lower().removeprefix("v")
        if not normalized_version or normalized_tag != normalized_version:
            return (
                False,
                "Update release tag does not match the announced version",
            )

        digest = str(expected_sha256).strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return False, "Update SHA-256 must contain exactly 64 hex characters"

        self.download_url = str(download_url).strip()
        self.expected_sha256 = digest
        return True, "Verified update artifact configured"

    def download_update(self, download_path: Optional[Path] = None) -> Tuple[bool, str]:
        """
        Download update ZIP file

        Args:
            download_path: Path to save the downloaded file

        Returns:
            Tuple of (success, message/error)
        """
        try:
            if not self.download_url or not self.expected_sha256:
                return (
                    False,
                    "Verified update metadata is unavailable; use the official "
                    "GitHub release page instead",
                )

            if download_path is None:
                download_path = self.cache_dir / f"update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            download_path = Path(download_path)
            download_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path = download_path.with_suffix(download_path.suffix + ".part")
            if partial_path.exists():
                partial_path.unlink()

            self.logger.info(f"Downloading update from: {self.download_url}")

            def _progress(downloaded: int, total: int):
                if total > 0 and downloaded % (1024 * 1024) == 0:
                    percent = (downloaded / total) * 100
                    self.logger.info(f"Download progress: {percent:.1f}%")

            download_to_file_sync(
                self.download_url,
                str(partial_path),
                timeout=60,
                progress=_progress,
                max_bytes=self.MAX_UPDATE_BYTES,
            )

            actual_sha256 = self._sha256(partial_path)
            if actual_sha256 != self.expected_sha256:
                partial_path.unlink(missing_ok=True)
                return (
                    False,
                    "Update checksum verification failed; the downloaded file was removed",
                )
            if not zipfile.is_zipfile(partial_path):
                partial_path.unlink(missing_ok=True)
                return False, "Verified update payload is not a valid ZIP archive"

            partial_path.replace(download_path)

            self.logger.info(f"Update downloaded successfully: {download_path}")
            return True, str(download_path)

        except HTTPStatusError as e:
            error_msg = f"Network error downloading update: {e}"
            self.logger.error(error_msg)
            return False, error_msg

        except Exception as e:
            if 'partial_path' in locals():
                partial_path.unlink(missing_ok=True)
            error_msg = f"Error downloading update: {e}"
            self.logger.error(error_msg)
            return False, error_msg

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _validate_archive(self, archive: zipfile.ZipFile) -> str:
        """Reject path traversal, links and zip bombs before extraction."""

        members = archive.infolist()
        if not members or len(members) > self.MAX_ARCHIVE_MEMBERS:
            raise ValueError("Unsafe update archive member count")

        top_levels = set()
        extracted_size = 0
        for member in members:
            raw_name = member.filename.replace("\\", "/")
            member_path = PurePosixPath(raw_name)
            if (
                not raw_name
                or member_path.is_absolute()
                or ".." in member_path.parts
            ):
                raise ValueError(f"Unsafe update archive path: {raw_name!r}")
            clean_parts = [part for part in member_path.parts if part not in {"", "."}]
            if not clean_parts:
                continue
            top_levels.add(clean_parts[0])

            file_type = (member.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise ValueError(f"Unsafe symbolic link in update: {raw_name!r}")

            extracted_size += member.file_size
            if extracted_size > self.MAX_EXTRACTED_BYTES:
                raise ValueError("Unsafe update archive expanded size")
            if (
                member.file_size > 10 * 1024 * 1024
                and member.compress_size > 0
                and member.file_size / member.compress_size > 200
            ):
                raise ValueError("Unsafe update archive compression ratio")

        if len(top_levels) != 1:
            raise ValueError("Unsafe update archive must contain one top-level folder")
        return next(iter(top_levels))

    @staticmethod
    def _restore_executable_permissions(
        archive: zipfile.ZipFile,
        staging: Path,
    ) -> None:
        """Restore only safe executable bits lost by ``ZipFile.extractall``."""

        for member in archive.infolist():
            archived_mode = (member.external_attr >> 16) & 0o777
            executable_bits = archived_mode & 0o111
            if member.is_dir() or not executable_bits:
                continue
            relative = PurePosixPath(member.filename.replace("\\", "/"))
            extracted = staging.joinpath(*relative.parts)
            if not extracted.is_file() or extracted.is_symlink():
                raise ValueError(
                    f"Executable update member was not extracted safely: "
                    f"{member.filename!r}"
                )
            current_mode = stat.S_IMODE(extracted.stat().st_mode)
            extracted.chmod(current_mode | executable_bits)

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

            extract_to = Path(extract_to)
            staging = extract_to.with_name(extract_to.name + ".tmp")
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True, exist_ok=True)

            self.logger.info(f"Extracting update to staging directory: {staging}")

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                top_level = self._validate_archive(zip_ref)
                zip_ref.extractall(staging)
                self._restore_executable_permissions(zip_ref, staging)

            staged_folder = staging / top_level
            if not staged_folder.is_dir():
                raise ValueError("No folders found in extracted update")

            backup = extract_to.with_name(extract_to.name + ".previous")
            if backup.exists():
                shutil.rmtree(backup)
            if extract_to.exists():
                extract_to.replace(backup)
            try:
                staging.replace(extract_to)
            except Exception:
                if backup.exists() and not extract_to.exists():
                    backup.replace(extract_to)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)

            actual_folder = extract_to / top_level
            self.logger.info(f"Update extracted successfully: {actual_folder}")
            return True, str(actual_folder)

        except zipfile.BadZipFile as e:
            if 'staging' in locals() and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            error_msg = f"Invalid ZIP file: {e}"
            self.logger.error(error_msg)
            return False, error_msg

        except Exception as e:
            if 'staging' in locals() and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
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
            # Compiled deployments contain the version module as code; they do
            # not need (and should not ship) a second editable version.py data
            # file beside the executable.
            try:
                from version import __version__ as module_version

                detected = str(module_version).strip()
                if detected:
                    self.logger.info(
                        "Detected local version from compiled module: %s",
                        detected,
                    )
                    return detected
            except (ImportError, AttributeError):
                pass

            # Try to find version.py in multiple possible locations
            possible_paths: list[Path] = [
                # From src/utils/ directory, go up to project root
                Path(__file__).parent.parent.parent / "version.py",
                # From current working directory
                Path.cwd() / "version.py",
            ]
            if sys.path:
                # From sys.path[0] (script directory)
                possible_paths.append(Path(sys.path[0]) / "version.py")

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
