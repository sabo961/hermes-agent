"""Tests for background side-result output through the prompt_toolkit-safe console."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermes_cli.cli_commands_mixin import _print_side_result_panel


def test_side_result_panel_prints_without_eager_tui_repaint_or_sleep():
    """Prompt-toolkit-safe printing must not be preceded by a forced repaint delay."""
    app = MagicMock()
    cli = SimpleNamespace(_app=app)
    console = MagicMock()

    with patch("builtins.print") as blank_line, \
         patch("hermes_cli.cli_commands_mixin.time.sleep") as sleep:
        _print_side_result_panel(
            cli,
            header_lines=("  Background result",),
            body="",
            title_suffix="complete",
            empty_note="  No result.",
            console=console,
        )

    app.invalidate.assert_not_called()
    sleep.assert_not_called()
    blank_line.assert_called_once_with()
    console.print.assert_any_call("  Background result")
    console.print.assert_any_call("  No result.")
