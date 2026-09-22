"""Response timestamps render in CLI chrome, never in model content."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _plain(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


class _SummerDateTime:
    @classmethod
    def now(cls):
        return datetime(
            2026, 9, 22, 20, 24, 31,
            tzinfo=timezone(timedelta(hours=2), "Central European Daylight Time"),
        )


class _WinterDateTime:
    @classmethod
    def now(cls):
        return datetime(
            2026, 1, 22, 20, 24, 31,
            tzinfo=timezone(timedelta(hours=1), "Central European Standard Time"),
        )


def test_streamed_response_timestamp_is_printed_after_the_box(monkeypatch):
    import cli as climod
    import hermes_cli.cli_timestamp as timestamp_mod
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.show_reasoning = False
    cli.final_response_markdown = "raw"
    cli.show_timestamps = True
    setattr(cli, "response_timestamp_position", "footer")
    cli.timestamp_format = "%Y-%m-%d %H:%M:%S UTC{utc_offset}"
    cli._reset_stream_state()
    emitted = []
    monkeypatch.setattr(timestamp_mod, "datetime", _SummerDateTime)
    assert timestamp_mod.formatted_response_timestamp(cli) == "2026-09-22 20:24:31 UTC+02:00"
    monkeypatch.setattr(climod, "_cprint", lambda value: emitted.append(value))
    monkeypatch.setattr(HermesCLI, "_scrollback_box_width", lambda self: 74)

    cli._stream_delta("Gotovo.\n")
    cli._flush_stream()
    assert not any("2026-09-22" in _plain(value) for value in emitted)
    turn = SimpleNamespace(
        result={"response_previewed": False},
        use_streaming_tts=False,
        box_opened=False,
    )
    cli._chat_print_response_panel(turn, "Gotovo.")

    lines = [_plain(value) for value in emitted]
    header = next(line for line in lines if "╭" in line)
    box_footer = next(index for index, line in enumerate(lines) if line.startswith("╰"))
    timestamp = next(index for index, line in enumerate(lines) if line == "[2026-09-22 20:24:31 UTC+02:00]")
    assert "2026-09-22" not in header
    assert timestamp > box_footer


def test_previewed_final_response_still_gets_one_footer_without_reprinting(monkeypatch):
    import cli as climod
    import hermes_cli.cli_timestamp as timestamp_mod
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.show_timestamps = True
    setattr(cli, "response_timestamp_position", "footer")
    cli.timestamp_format = "%H:%M"
    emitted = []
    panels = []

    class _Console:
        def print(self, value):
            panels.append(value)

    monkeypatch.setattr(timestamp_mod, "datetime", _SummerDateTime)
    monkeypatch.setattr(climod, "_cprint", lambda value: emitted.append(value))
    monkeypatch.setattr(climod, "ChatConsole", lambda: _Console())
    turn = SimpleNamespace(
        result={"response_previewed": True},
        use_streaming_tts=False,
        box_opened=False,
    )

    cli._chat_print_response_panel(turn, "Fallback already shown.")

    assert panels == []
    assert [_plain(value) for value in emitted] == ["[20:24]"]


def test_verbose_windows_timezone_name_is_not_relabelled_as_a_false_acronym(monkeypatch):
    import hermes_cli.cli_timestamp as timestamp_mod

    cli = SimpleNamespace(timestamp_format="%Z")
    monkeypatch.setattr(timestamp_mod, "datetime", _WinterDateTime)

    assert timestamp_mod.formatted_response_timestamp(cli) == "Central European Standard Time"


def test_invalid_response_timestamp_position_falls_back_to_label():
    from hermes_cli.cli_timestamp import resolve_response_timestamp_position

    assert resolve_response_timestamp_position("footer") == "footer"
    assert resolve_response_timestamp_position("both") == "both"
    assert resolve_response_timestamp_position("nonsense") == "label"
    assert resolve_response_timestamp_position(None) == "label"


@pytest.mark.parametrize("path", ["nonstream", "tts", "transformed", "error"])
def test_final_response_paths_emit_exactly_one_footer(path, monkeypatch):
    import cli as climod
    import hermes_cli.cli_timestamp as timestamp_mod
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.show_timestamps = True
    setattr(cli, "response_timestamp_position", "footer")
    cli.timestamp_format = "%H:%M"
    cli.final_response_markdown = "raw"
    cli._stream_started = path == "transformed"
    cli._stream_box_opened = path == "transformed"
    emitted = []
    panels = []

    class _Console:
        def print(self, value):
            panels.append(value)

    monkeypatch.setattr(timestamp_mod, "datetime", _SummerDateTime)
    monkeypatch.setattr(climod, "_cprint", lambda value: emitted.append(value))
    monkeypatch.setattr(climod, "ChatConsole", lambda: _Console())
    monkeypatch.setattr(HermesCLI, "_scrollback_box_width", lambda self: 74)
    if path == "transformed":
        monkeypatch.setattr(climod, "_post_stream_transform_output", lambda response, result: "transformed")

    result = {"response_previewed": False}
    if path == "error":
        result["failed"] = True
    turn = SimpleNamespace(
        result=result,
        use_streaming_tts=path == "tts",
        box_opened=path == "tts",
    )

    cli._chat_print_response_panel(turn, "Done.")

    plain = [_plain(value) for value in emitted]
    assert plain.count("[20:24]") == 1
    if path in {"nonstream", "error"}:
        assert len(panels) == 1
    elif path == "tts":
        assert any(line.startswith("\n╰") for line in plain)
    else:
        assert "transformed" in plain


def test_empty_response_emits_no_footer(monkeypatch):
    import cli as climod
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.show_timestamps = True
    setattr(cli, "response_timestamp_position", "footer")
    emitted = []
    monkeypatch.setattr(climod, "_cprint", lambda value: emitted.append(value))
    turn = SimpleNamespace(result={"response_previewed": False}, use_streaming_tts=False, box_opened=False)

    cli._chat_print_response_panel(turn, "")

    assert emitted == []


def test_default_label_position_keeps_timestamp_in_header_only(monkeypatch):
    import cli as climod
    import hermes_cli.cli_timestamp as timestamp_mod
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.show_reasoning = False
    cli.final_response_markdown = "raw"
    cli.show_timestamps = True
    setattr(cli, "response_timestamp_position", "label")
    cli.timestamp_format = "%H:%M"
    cli._reset_stream_state()
    emitted = []
    monkeypatch.setattr(timestamp_mod, "datetime", _SummerDateTime)
    monkeypatch.setattr(climod, "_cprint", lambda value: emitted.append(value))
    monkeypatch.setattr(HermesCLI, "_scrollback_box_width", lambda self: 74)

    cli._stream_delta("Done.\n")
    cli._flush_stream()
    turn = SimpleNamespace(result={"response_previewed": False}, use_streaming_tts=False, box_opened=False)
    cli._chat_print_response_panel(turn, "Done.")

    plain = [_plain(value) for value in emitted]
    header = next(line for line in plain if "╭" in line)
    assert "☤ Hermes" in header and "20:24" in header
    assert "[20:24]" not in plain
