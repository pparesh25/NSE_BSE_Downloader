"""
Version Information

Contains application version and build information.
"""

__version__ = "1.1.0"
__build_date__ = "2026-08-09"
__build_number__ = 27

# Verified update artifacts, keyed by "<platform>-<architecture>" exactly as
# package_release_artifact.py names each release archive.  Release automation
# fills one entry per published asset, with the URL and its SHA-256 written
# together.
#
# The updater stays notification-only for any platform with no entry, an empty
# URL, or an empty digest, rather than executing code without an integrity
# check.  An empty mapping therefore makes every platform notification-only,
# which is the intended state until a release is published.
#
# Do not rename __version__, __build_date__ or VERSION_HISTORY.  Installed
# v1.0.1 clients parse those three by regular expression to discover this
# release, and tests/test_index_and_version.py fails if that contract breaks.
__update_artifacts__: dict[str, dict[str, str]] = {
    # "darwin-arm64": {"url": "https://github.com/.../NSE_BSE_Downloader-1.1.0-darwin-arm64.zip",
    #                  "sha256": "<64 hex characters>"},
    # "windows-x64": {...},
    # "linux-x64": {...},
}

# Version history
VERSION_HISTORY = {

    "1.1.0": {
        "release_date": "2026-08-09",
        "features": [
            "One date-aware downloader path for every legacy and current NSE/BSE report URL",
            "Stable 9-column NSE/BSE equity output with delivery quantity and percentage",
            "Stable 9-column NSE futures output with open interest and change in OI",
            "Per-symbol text histories with NSE/BSE split, consolidation and bonus adjustments",
            "Pending-delivery retry, corporate-action audit ledger and rebuildable raw snapshots",
            "Strict source schema/date validation with quarantined invalid reports",
            "Resumable per-date pipeline manifest with partial-success reporting and gap repair",
            "Deterministic restart-safe combined EQ, SME and Index bhavcopy assembly",
            "Fail-closed state quarantine and command-line symbol history repair",
            "Calendar date-range selection and individually collapsible GUI sections",
            "IST-aware official NSE holiday calendar with daily refresh and stale-cache fallback",
            "Remembered automatic-update, skipped-version and validated effective settings",
            "Cooperative GUI cancellation with typed per-segment completion outcomes",
            "Nuitka-safe resource paths, stable app identity and guarded packaging dry run",
            "Responsive default GUI layout with readable macOS source-process identity",
            "Python 3.10/3.13 CI quality gates with enforced 70 percent coverage",
            "Staged-only per-date publication with stable canonical output columns",
            "Self-identifying symbol histories carrying the security ISIN",
            "One writer per data folder, so two copies cannot overwrite each other"
        ],
        "bug_fixes": [
            "Fixed NSE SME filename switch from two-digit to four-digit year",
            "Matched NSE delivery by symbol and series and BSE delivery by security code",
            "Rejected HTML error pages returned with successful HTTP status",
            "Rejected JSON, header-only, wrong-date and invalid-numeric market reports",
            "Detected middle-date gaps, corrupt daily files and incomplete pipeline stages",
            "Kept daily and symbol files valid through atomic replacement",
            "Recovered interrupted corporate actions without double-adjusting prices",
            "Prevented task arrival order or a failed dependency from publishing partial combined EQ files",
            "Removed forced QThread termination during Stop and application close",
            "Prevented stale hard-coded holidays and host-timezone drift from selecting the wrong trading date",
            "Preserved update preferences through the same config and user-setting precedence path",
            "Removed working-directory and duplicate version-data assumptions from packaged execution",
            "Kept both date controls and the Donate action visible in the default window",
            "Read every adjustment in one corporate action, not only the first",
            "Moved traded and delivered quantities with a split, keeping turnover intact",
            "Kept a thinly traded day from being rewritten as an untraded day",
            "Preferred an exchange's corrected bhavcopy over the row it replaces",
            "Stopped a reused ticker from appending into a delisted company's history",
            "Refused to merge two histories that claim the same trading dates",
            "Kept superseded symbol files in quarantine instead of deleting them",
            "Rejected placeholder and truncated daily reports instead of publishing them",
            "Wrote every output file with one line ending on all platforms"
        ]
    },

    "1.0.1": {
        "release_date": "2025-08-07",
        "features": [
            "Enhanced download logging system",
            "Fixed unnecessary 'file not available' logs for current date",
            "Improved console output during market hours",
            "Better user experience with cleaner logs"
        ],
        "bug_fixes": [
            "Prevented current date download attempts before 6:00 PM",
            "Eliminated redundant error logs in IDE console",
            "Fixed console spam during trading hours"
        ]
    },

    "1.0.0": {
        "release_date": "2025-07-31",
        "features": [
            "Initial release",
            "NSE-EQ, NSE-INDEX, NSE-FO, NSE-SME downloaders",
            "BSE-EQ, BSE-INDEX downloaders",
            "Smart Append Operations",
            "Professional GUI interface",
            "Customizable Settings",
            "File management system",
            "Logging and error handling",
            "Unit tests and code coverage",
            "Automatic update checking",
            "Memory optimization",
            "Async download processing"
        ]
    }
}

def get_version():
    """Get current version string"""
    return __version__

def get_build_info():
    """Get build information"""
    return {
        "version": __version__,
        "build_date": __build_date__,
        "build_number": __build_number__
    }

def get_version_history():
    """Get version history"""
    return VERSION_HISTORY
