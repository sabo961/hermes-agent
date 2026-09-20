from unittest.mock import MagicMock

from cli import HermesCLI


def test_continue_dispatches_to_manual_context_rotation():
    cli = HermesCLI.__new__(HermesCLI)
    cli._pending_resume_sessions = None
    cli._manual_compress = MagicMock()

    assert cli.process_command("/continue") is True

    cli._manual_compress.assert_called_once_with("/continue")


def test_continue_rejects_arguments_instead_of_treating_them_as_focus(capsys):
    cli = HermesCLI.__new__(HermesCLI)
    cli._pending_resume_sessions = None
    cli._manual_compress = MagicMock()

    assert cli.process_command("/continue cadence") is True

    cli._manual_compress.assert_not_called()
    output = capsys.readouterr().out
    assert "/continue takes no arguments" in output
    assert "/resume cadence" in output
    assert "/compress cadence" in output
