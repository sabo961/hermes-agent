from unittest.mock import MagicMock

from cli import HermesCLI
from hermes_cli.commands import COMMAND_REGISTRY, command_help_lines, resolve_command


def _make_cli():
    cli = HermesCLI.__new__(HermesCLI)
    cli._pending_resume_sessions = None
    manual_compress = MagicMock()
    cli._manual_compress = manual_compress
    return cli, manual_compress


def _command(name):
    command = resolve_command(name)
    assert command is not None
    return command


def test_every_registered_command_has_central_help():
    for command in COMMAND_REGISTRY:
        lines = command_help_lines(command)
        text = "\n".join(lines)
        assert lines[0].startswith("Usage: /")
        assert f"/{command.name}" in lines[0]
        assert command.description in text
        assert command.category in text
        if command.args_hint:
            assert command.args_hint in lines[0]
        else:
            assert "Arguments: none" in text


def test_central_help_reports_each_availability_mode_accurately():
    cli_only = "\n".join(command_help_lines(_command("continue")))
    gateway_only = "\n".join(command_help_lines(_command("topic")))
    shared = "\n".join(command_help_lines(_command("compress")))
    config_gated = "\n".join(command_help_lines(_command("verbose")))

    assert "Availability: CLI only" in cli_only
    assert "Availability: messaging gateway only" in gateway_only
    assert "Availability: CLI and messaging gateway" in shared
    assert "Availability: CLI; messaging gateway when display.tool_progress_command is enabled" in config_gated


def test_central_help_lists_aliases_and_subcommands():
    aliases = "\n".join(command_help_lines(_command("compress")))
    subcommands = "\n".join(command_help_lines(_command("voice")))

    assert "Aliases: /compact" in aliases
    assert "Subcommands: on, off, tts, status" in subcommands


def test_command_help_flag_is_intercepted_before_handler(capsys):
    cli, manual_compress = _make_cli()

    assert cli.process_command("/continue --help") is True

    manual_compress.assert_not_called()
    output = capsys.readouterr().out
    assert "Usage: /continue" in output
    assert "Arguments: none" in output


def test_alias_short_help_resolves_to_canonical_command(capsys):
    cli, manual_compress = _make_cli()

    assert cli.process_command("/compact -h") is True

    manual_compress.assert_not_called()
    output = capsys.readouterr().out
    assert "Usage: /compress" in output
    assert "Requested as: /compact" in output


def test_help_flag_still_fires_pre_command_hook(monkeypatch, capsys):
    from hermes_cli import plugins as plugins_mod

    captured = {}
    monkeypatch.setattr(
        plugins_mod, "fire_pre_command_hook", lambda **kwargs: captured.update(kwargs))
    cli, manual_compress = _make_cli()

    assert cli.process_command("/compact --help") is True

    manual_compress.assert_not_called()
    capsys.readouterr()
    assert captured["command"] == "compress"
    assert captured["alias_used"] == "compact"
    assert captured["args_raw"] == "--help"


def test_help_exact_command_uses_same_central_renderer(capsys):
    cli, _manual_compress = _make_cli()
    cli.config = {}

    cli.show_help("continue")

    output = capsys.readouterr().out
    assert "Usage: /continue" in output
    assert "Arguments: none" in output
