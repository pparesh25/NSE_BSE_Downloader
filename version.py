"""
Version Information

Contains application version and build information.
"""

__version__ = "1.0.2-dev"
__build_date__ = "2025-08-07"
__build_number__ = 22

# Version history
VERSION_HISTORY = {

    "1.0.2-dev": {
        "release_date": "2025-08-07",
        "features": [
            "Development branch initialized",
            "Structured version management system",
            "Clean git history established"
        ],
        "type": "development",
        "status": "in_development"
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
