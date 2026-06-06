"""Cross-platform command helpers.

All shell-facing code should pass through this module when it needs command
splitting, display-friendly joining, script invocation, or log-follow commands.
That keeps Windows quoting and PowerShell/cmd edge cases out of provider,
runtime, and CLI code.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Any


def system_name() -> str:
    return platform.system().lower()


def is_windows() -> bool:
    return os.name == "nt" or system_name().startswith("win")


def is_macos() -> bool:
    return system_name() == "darwin"


def is_linux() -> bool:
    return system_name() == "linux"


def split_command_line(command: str) -> list[str]:
    """Split a user command using the host platform's quoting rules."""
    return shlex.split(command, posix=not is_windows())


def shell_join(command: list[str]) -> str:
    """Render a command for display or tmux while preserving spaces safely."""
    if is_windows():
        return subprocess_list_to_windows_shell(command)
    return shlex.join(command)


def subprocess_list_to_windows_shell(command: list[str]) -> str:
    return " ".join(_quote_windows_arg(part) for part in command)


def _quote_windows_arg(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() or char in {'"', "'"} for char in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def powershell_executable() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def script_invocation(command: str, args: tuple[str, ...]) -> list[str]:
    """Build the subprocess argv needed to run scripts on the current OS."""
    suffix = Path(command).suffix.lower()
    if suffix == ".ps1":
        return [powershell_executable(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(command), *args]
    if suffix in {".cmd", ".bat"} and is_windows():
        return ["cmd", "/c", str(PureWindowsPath(command)), *args]
    return [command, *args]


def hidden_subprocess_kwargs(*, background: bool = False, new_process_group: bool = False) -> dict[str, Any]:
    """Return Windows-only subprocess kwargs that avoid visible console windows."""
    if not is_windows():
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if background or new_process_group:
        # Do not combine DETACHED_PROCESS with CREATE_NO_WINDOW: Windows ignores
        # the no-window flag in that case on some terminal configurations.
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {"creationflags": creationflags, "startupinfo": startupinfo}


def follow_file_command(path: Path) -> list[str]:
    """Return a portable command that follows appended log output."""
    if is_windows():
        escaped = str(path).replace("'", "''")
        return [
            powershell_executable(),
            "-NoProfile",
            "-Command",
            f"Get-Content -LiteralPath '{escaped}' -Wait",
        ]
    return ["tail", "-f", path.as_posix()]
