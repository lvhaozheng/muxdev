"""Rich render helpers shared by CLI and TUI surfaces."""

from __future__ import annotations

from rich.table import Table

from ..providers import ProviderProbe


COLUMNS = (
    "provider",
    "mode",
    "command",
    "installed",
    "version",
    "headless",
    "pty",
    "json",
    "approval",
    "skill",
    "attach",
    "status",
    "notes",
)


def provider_table(probes: list[ProviderProbe]) -> Table:
    table = Table(title="M0 Provider Capability Matrix")
    for column in COLUMNS:
        if column in {"command", "notes"}:
            table.add_column(column, min_width=len(column), overflow="fold")
        else:
            table.add_column(column, min_width=len(column), no_wrap=True)

    for probe in probes:
        row = probe.to_dict()
        table.add_row(*(format_cell(row[column]) for column in COLUMNS))
    return table


def format_cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
