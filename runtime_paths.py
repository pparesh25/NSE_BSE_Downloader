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
