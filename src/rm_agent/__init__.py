"""Package entrypoint for rm-agent CLI."""

from main import run_server


def main() -> None:
    """Launch the FastAPI server via CLI entrypoint."""
    run_server()


__all__ = ["main"]
