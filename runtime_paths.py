"""Runtime paths shared by source runs and Nuitka deployments."""

from pathlib import Path


def application_root() -> Path:
    """Return the read-only root containing bundled application resources."""

    # Nuitka's standalone/onefile runtime file-reference mode resolves this
    # module beside the bundled config and source-package resource tree.
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    """Resolve a bundled resource without depending on the working directory."""

    relative = Path(*parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("resource path must remain inside the application root")
    return application_root() / relative


def default_config_path() -> Path:
    """Return the bundled default configuration path."""

    return resource_path("config.yaml")


def user_state_dir() -> Path:
    """Return the writable per-user directory for preferences and diagnostics.

    Deliberately not the data root.  Everything under it is regenerable; the
    data root holds years of downloaded market history and nothing here may
    grow inside it.
    """

    return Path.home() / ".nse_bse_downloader"


def log_directory() -> Path:
    """Return the directory application logs are written to."""

    return user_state_dir() / "logs"
