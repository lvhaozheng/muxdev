"""Provider account and login metadata loaded from dynamic configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .loader import load_config
from ..providers.registry import get_provider_definition


@dataclass(frozen=True)
class AccountInfo:
    """Human guidance for signing up, authenticating, and reading provider docs."""

    provider: str
    required: bool
    signup_url: str
    docs_url: str
    login_command: str
    notes: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


ACCOUNT_INFOS: dict[str, AccountInfo] = {}


def get_account_info(provider: str) -> AccountInfo:
    """Return account guidance for a configured provider."""
    definition = get_provider_definition(provider)
    data = load_config().get("accounts", {}).get(definition.provider, {})
    if not isinstance(data, dict):
        data = {}
    return AccountInfo(
        provider=definition.provider,
        required=bool(data.get("required", False)),
        signup_url=str(data.get("signup_url", "")),
        docs_url=str(data.get("docs_url", "")),
        login_command=str(data.get("login_command", "")),
        notes=str(data.get("notes", "")),
    )


ACCOUNT_INFOS = {definition.provider: get_account_info(definition.provider) for definition in ()}
