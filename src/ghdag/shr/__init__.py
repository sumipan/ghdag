"""ghdag.shr — Self-hosted runner management package."""

from ghdag.shr.config import ShrConfig, load_config, save_config
from ghdag.shr.daemon import install_procfile_entry, uninstall_procfile_entry, start, stop, is_running
from ghdag.shr.github import get_registration_token, get_removal_token, get_runner_status
from ghdag.shr.runner import download_runner, configure_runner, remove_runner

__all__ = [
    "ShrConfig",
    "load_config",
    "save_config",
    "install_procfile_entry",
    "uninstall_procfile_entry",
    "start",
    "stop",
    "is_running",
    "get_registration_token",
    "get_removal_token",
    "get_runner_status",
    "download_runner",
    "configure_runner",
    "remove_runner",
]
