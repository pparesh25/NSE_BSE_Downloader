"""
Version Information

Contains application version and build information.
"""

__version__ = "1.0.0"
__build_date__ = "2024-12-01"
__build_number__ = 100

# Version history
VERSION_HISTORY = {    
    
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
