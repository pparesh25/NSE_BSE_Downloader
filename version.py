"""
Version Information

Contains application version and build information.
"""

__version__ = "1.1.0"
__build_date__ = "2025-08-05"
__build_number__ = 22

# Version history
VERSION_HISTORY = {    
    
    "1.1.0": {
        "release_date": "2025-08-05",
        "features": [
            
            "exprimental update service testing no new updates available"
            
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
