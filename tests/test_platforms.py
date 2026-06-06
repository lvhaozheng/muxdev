from __future__ import annotations

from pathlib import Path

import muxdev.core.platforms as platforms


def test_script_invocation_wraps_cmd_only_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(platforms, "is_windows", lambda: True)
    assert platforms.script_invocation("C:/node/npm.CMD", ("install",)) == ["cmd", "/c", "C:\\node\\npm.CMD", "install"]

    monkeypatch.setattr(platforms, "is_windows", lambda: False)
    assert platforms.script_invocation("/opt/bin/npm.cmd", ("install",)) == ["/opt/bin/npm.cmd", "install"]


def test_split_command_line_uses_platform_quoting(monkeypatch) -> None:
    monkeypatch.setattr(platforms, "is_windows", lambda: False)
    assert platforms.split_command_line("python -c 'print(1)'") == ["python", "-c", "print(1)"]

    monkeypatch.setattr(platforms, "is_windows", lambda: True)
    assert platforms.split_command_line('python -c "print(1)"') == ["python", "-c", '"print(1)"']


def test_follow_file_command_is_platform_specific(monkeypatch) -> None:
    monkeypatch.setattr(platforms, "is_windows", lambda: False)
    assert platforms.follow_file_command(Path("/tmp/log.txt")) == ["tail", "-f", "/tmp/log.txt"]

    monkeypatch.setattr(platforms, "is_windows", lambda: True)
    command = platforms.follow_file_command(Path("C:/tmp/log.txt"))
    assert command[1] == "-NoProfile"
    assert "Get-Content" in command[-1]
