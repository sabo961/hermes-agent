from unittest.mock import MagicMock

from cli import HermesCLI


def test_continue_dispatches_to_manual_context_rotation():
    cli = HermesCLI.__new__(HermesCLI)
    cli._pending_resume_sessions = None
    cli._manual_compress = MagicMock()

    assert cli.process_command("/continue") is True

    cli._manual_compress.assert_called_once_with("/continue")
