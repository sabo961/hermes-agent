"""Behavior contracts for the grouped CLI MCP slash command."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from cli import HermesCLI
from hermes_cli.commands import SUBCOMMANDS, resolve_command
from hermes_cli.commands_completion import SlashCommandCompleter


def _cli() -> HermesCLI:
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = {}
    cli_obj.session_id = "mcp-test-session"
    cli_obj._pending_resume_sessions = None
    return cli_obj


def _completion_texts(text: str) -> list[str]:
    completer = SlashCommandCompleter()
    return [
        completion.text
        for completion in completer.get_completions(
            Document(text=text), CompleteEvent(completion_requested=True)
        )
    ]


def test_mcp_registry_declares_status_and_reload_subcommands_for_autocomplete():
    command = resolve_command("mcp")

    assert command is not None
    assert command.name == "mcp"
    assert command.cli_only is True
    assert command.subcommands == ("status", "reload")
    assert SUBCOMMANDS["/mcp"] == ["status", "reload"]
    assert "mcp" in _completion_texts("/mc")


def test_mcp_status_renders_connected_and_failed_servers(monkeypatch, capsys):
    cli_obj = _cli()
    monkeypatch.setattr(
        "tools.mcp_tool_discovery.get_mcp_status",
        lambda: [
            {"name": "filesystem", "transport": "stdio", "status": "connected", "tools": 3},
            {"name": "remote", "transport": "http", "status": "failed", "tools": 0},
        ],
    )

    assert cli_obj.process_command("/mcp status") is True

    output = capsys.readouterr().out
    assert "filesystem" in output
    assert "stdio" in output
    assert "connected" in output
    assert "3 tool(s)" in output
    assert "remote" in output
    assert "http" in output
    assert "failed" in output
    assert "0 tool(s)" in output


def test_mcp_status_reports_when_no_servers_are_configured(monkeypatch, capsys):
    cli_obj = _cli()
    monkeypatch.setattr("tools.mcp_tool_discovery.get_mcp_status", lambda: [])

    assert cli_obj.process_command("/mcp status") is True

    assert "No MCP servers configured." in capsys.readouterr().out


def test_mcp_without_subcommand_prints_concise_usage(monkeypatch, capsys):
    cli_obj = _cli()
    monkeypatch.setattr("tools.mcp_tool_discovery.get_mcp_status", lambda: [])

    assert cli_obj.process_command("/mcp") is True

    output = capsys.readouterr().out
    assert "Usage: /mcp [status|reload]" in output
    assert "/mcp status" in output
    assert "/mcp reload" in output


def test_mcp_rejects_invalid_subcommand(capsys):
    cli_obj = _cli()

    assert cli_obj.process_command("/mcp unknown") is True

    output = capsys.readouterr().out
    assert "Unknown MCP subcommand: unknown" in output
    assert "Usage: /mcp [status|reload]" in output


def test_mcp_reports_unexpected_arguments_for_valid_subcommands(capsys):
    cli_obj = _cli()

    for command, subcommand in (
        ("/mcp status extra", "status"),
        ("/mcp reload extra", "reload"),
    ):
        assert cli_obj.process_command(command) is True
        output = capsys.readouterr().out
        assert f"Unexpected arguments for /mcp {subcommand}: extra" in output
        assert "Usage: /mcp [status|reload]" in output
        assert "Unknown MCP subcommand" not in output


def test_mcp_reload_delegates_to_existing_reload_confirmation_path():
    cli_obj = _cli()

    with patch.object(cli_obj, "_confirm_and_reload_mcp") as reload_command:
        assert cli_obj.process_command("/mcp reload") is True

    reload_command.assert_called_once_with("/reload-mcp")


def test_reload_mcp_always_prints_active_server_names(monkeypatch, capsys):
    import tools.mcp_tool as mcp_tool

    cli_obj = _cli()
    cli_obj._command_running = False
    cli_obj.agent = None
    cli_obj.enabled_toolsets = None
    cli_obj.conversation_history = []

    monkeypatch.setattr(mcp_tool, "_servers", {"filesystem": object()})
    monkeypatch.setattr(mcp_tool, "_lock", threading.RLock())
    monkeypatch.setattr("tools.mcp_tool_lifecycle.shutdown_mcp_servers", lambda: None)
    monkeypatch.setattr("tools.mcp_tool_discovery.discover_mcp_tools", lambda: ["mcp_filesystem_read"])
    monkeypatch.setattr("tools.mcp_tool_discovery.get_mcp_status", lambda: [
        {"name": "filesystem", "transport": "stdio", "status": "connected", "connected": True, "tools": 1},
    ])
    monkeypatch.setattr("tools.mcp_tool_agent.reprobe_tool_availability", lambda: None)

    cli_obj._reload_mcp()

    output = capsys.readouterr().out
    assert "Active MCP servers: filesystem" in output
