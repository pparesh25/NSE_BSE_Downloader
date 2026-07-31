"""
Version Information

Contains application version and build information.
"""

__version__ = "1.1.0"
__build_date__ = "2026-07-31"
__build_number__ = 23

# Release automation must replace both values together.  The desktop updater
# remains notification-only while either field is blank, rather than executing
# code from a mutable branch without an integrity check.
__update_url__ = ""
__update_sha256__ = ""

# Version history
VERSION_HISTORY = {

    "1.1.0": {
        "release_date": "2026-07-31",
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
            "Legacy 7-column compatibility option"
        ],
        "bug_fixes": [
            "Fixed NSE SME filename switch from two-digit to four-digit year",
            "Matched NSE delivery by symbol and series and BSE delivery by security code",
            "Rejected HTML error pages returned with successful HTTP status",
            "Rejected JSON, header-only, wrong-date and invalid-numeric market reports",
            "Detected middle-date gaps, corrupt daily files and incomplete pipeline stages",
            "Kept daily and symbol files valid through atomic replacement",
            "Recovered interrupted corporate actions without double-adjusting prices",
            "Prevented task arrival order or a failed dependency from publishing partial combined EQ files"
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
