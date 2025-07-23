"""
Version Information

Contains application version and build information.
"""

__version__ = "2.0.0"
__build_date__ = "2025-01-23"
__build_number__ = 200

# Version history
VERSION_HISTORY = {
    "2.0.0": {
        "release_date": "2025-01-23",
        "features": [
            "Added BSE INDEX downloader support",
            "Implemented fast download strategy (5-10x faster)",
            "Added GitHub market holidays integration",
            "Enhanced GUI with dynamic options",
            "Added response timeout configuration",
            "Implemented memory-based file processing",
            "Added update checker system"
        ],
        "bug_fixes": [
            "Fixed application freeze on stop download",
            "Resolved BSE INDEX column ordering issue",
            "Fixed console log clearing after download complete",
            "Improved GUI flickering during downloads"
        ]
    },
    "1.0.0": {
        "release_date": "2024-12-01",
        "features": [
            "Initial release",
            "NSE EQ, FO, SME downloaders",
            "BSE EQ downloader",
            "Basic GUI interface",
            "File management system"
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
