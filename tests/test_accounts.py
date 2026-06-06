from __future__ import annotations

from muxdev.config.accounts import get_account_info


def test_supported_provider_account_info() -> None:
    info = get_account_info("claude-code")

    assert info.required is True
    assert info.signup_url == "https://claude.ai/"
    assert info.login_command == "claude"


def test_mock_account_info() -> None:
    info = get_account_info("mock")

    assert info.required is False
    assert info.signup_url == ""
