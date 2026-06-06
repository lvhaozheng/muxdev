"""Dynamic muxdev configuration helpers."""

from .loader import config_sources, load_config, path_config, validate_config

__all__ = [
    "AccountInfo",
    "InstallPlan",
    "InstallResult",
    "InstallStatus",
    "config_sources",
    "get_account_info",
    "get_install_plan",
    "install_provider",
    "load_config",
    "path_config",
    "validate_config",
]


def __getattr__(name: str):
    if name in {"AccountInfo", "get_account_info"}:
        from .accounts import AccountInfo, get_account_info

        return {"AccountInfo": AccountInfo, "get_account_info": get_account_info}[name]
    if name in {"InstallPlan", "InstallResult", "InstallStatus", "get_install_plan", "install_provider"}:
        from .installers import InstallPlan, InstallResult, InstallStatus, get_install_plan, install_provider

        return {
            "InstallPlan": InstallPlan,
            "InstallResult": InstallResult,
            "InstallStatus": InstallStatus,
            "get_install_plan": get_install_plan,
            "install_provider": install_provider,
        }[name]
    raise AttributeError(name)
