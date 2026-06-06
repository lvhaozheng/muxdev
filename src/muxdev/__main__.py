"""`python -m muxdev` entrypoint that delegates to the Typer CLI app."""

from .cli import app


if __name__ == "__main__":
    app()
