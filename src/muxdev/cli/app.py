"""Compatibility module for the Typer app object.

The CLI implementation lives in :mod:`muxdev.cli.main`; this module exists so
older in-process imports of ``muxdev.cli.app`` still resolve to the canonical
app object while no longer carrying command implementation code.
"""

from .main import app

__all__ = ["app"]
