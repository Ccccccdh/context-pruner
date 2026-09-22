"""Run the installed Context-Pruner CLI from a Codex plugin workflow."""

try:
    from context_pruner.cli import main
except ImportError as error:
    raise SystemExit(
        "Context-Pruner is not installed. Run: python -m pip install -e <repository>/code"
    ) from error


if __name__ == "__main__":
    raise SystemExit(main())
