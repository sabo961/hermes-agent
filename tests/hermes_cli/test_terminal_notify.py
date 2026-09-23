"""display.bell_on_prompt / bell_on_complete also drive OSC 9 + Warp OSC 777 via _ring_bell."""

import json
import sys

import pytest

from cli import HermesCLI
from hermes_cli import terminal_notify

_WARP_OK = {
    "TERM_PROGRAM": "WarpTerminal",
    "WARP_CLI_AGENT_PROTOCOL_VERSION": "1",
    "WARP_CLIENT_VERSION": "v0.2026.08.01.00.00.stable_01",
}


def _ring(monkeypatch, *, flag_on, env, **kwargs):
    for key in _WARP_OK:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    written = []
    monkeypatch.setattr(terminal_notify, "write_tty", written.append)
    cli = HermesCLI.__new__(HermesCLI)
    cli.bell_on_prompt = flag_on
    cli.session_id = "sess-1"
    cli._ring_bell(prompt=True, **kwargs)
    return "".join(written)


def test_osc9_body_emitted_and_sanitized_only_when_flag_on(monkeypatch):
    out = _ring(monkeypatch, flag_on=True, env={}, context="approval\x1b\x07\x00\x7f!")
    assert out == "\a\x1b]9;Hermes: approval!\x07"
    assert _ring(monkeypatch, flag_on=False, env={}, context="approval") == ""


def test_warp_osc777_only_under_supported_warp_build(monkeypatch):
    out = _ring(monkeypatch, flag_on=True, env=_WARP_OK, context="approval", detail="rm -rf build")
    prefix = "\x1b]777;notify;warp://cli-agent;"
    assert out.count(prefix) == 1
    payload = json.loads(out.split(prefix, 1)[1].rstrip("\x07"))
    assert payload["agent"] == "hermes"
    assert payload["event"] == "permission_request"
    assert payload["summary"] == "rm -rf build"
    assert payload["session_id"] == "sess-1"
    assert payload["v"] == 1
    # Broken build (advertises the protocol var but can't render) → OSC 9 only.
    broken = dict(_WARP_OK, WARP_CLIENT_VERSION="v0.2026.03.25.08.24.stable_05")
    assert prefix not in _ring(monkeypatch, flag_on=True, env=broken, context="approval")
    # Not Warp at all → OSC 9 only.
    not_warp = dict(_WARP_OK, TERM_PROGRAM="ghostty")
    assert prefix not in _ring(monkeypatch, flag_on=True, env=not_warp, context="approval")


def test_running_app_gets_bell_and_osc9_on_its_loop_never_a_second_tty_writer(monkeypatch):
    """With the prompt_toolkit app live, the bell + notification must reach the tty through the
    app's output ON THE APP LOOP, never via a second writer from the calling thread."""
    for key in _WARP_OK:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(terminal_notify, "write_tty", lambda seq: pytest.fail(f"stray tty write: {seq!r}"))

    class _Output:
        raw = []

        def write_raw(self, data):
            self.raw.append(data)

        def flush(self):
            self.raw.append("<flush>")

    class _Loop:
        queued = []

        def call_soon_threadsafe(self, fn):
            self.queued.append(fn)

    class _App:
        _is_running = True
        loop = _Loop()
        output = _Output()

    cli = HermesCLI.__new__(HermesCLI)
    cli.bell_on_complete = True
    cli.session_id = "sess-1"
    cli._app = _App()
    cli._ring_bell(context="turn complete")
    # Nothing touched the tty from the calling thread; the write is queued for the loop.
    assert _Output.raw == []
    assert len(_Loop.queued) == 1
    _Loop.queued[0]()
    assert _Output.raw == ["\a\x1b]9;Hermes: turn complete\x07", "<flush>"]


def test_prompt_voice_alert_launches_zec_say_once_per_quiet_window(tmp_path, monkeypatch):
    script = tmp_path / "bin" / "zec-say.py"
    script.parent.mkdir()
    script.write_text("# probe\n", encoding="utf-8")
    launched = []

    monkeypatch.setattr(terminal_notify, "_prompt_voice_last", 0.0, raising=False)
    monkeypatch.setattr(terminal_notify.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: tmp_path)
    monkeypatch.setattr(
        terminal_notify.subprocess,
        "Popen",
        lambda args, **kwargs: launched.append((args, kwargs)),
    )

    assert terminal_notify.speak_prompt_attention("approval", "gateway stop") is True
    assert terminal_notify.speak_prompt_attention("approval", "gateway stop") is False
    assert len(launched) == 1
    args, kwargs = launched[0]
    assert args == [
        sys.executable,
        str(script),
        "Zec ovdje. Trebam tvoje odobrenje u terminalu.",
    ]
    assert kwargs["stdin"] is terminal_notify.subprocess.DEVNULL
    assert kwargs["stdout"] is terminal_notify.subprocess.DEVNULL
    assert kwargs["stderr"] is terminal_notify.subprocess.DEVNULL


def test_title_attention_prefixes_and_restores_the_live_console_title(monkeypatch):
    current = ["Hermes · cadence"]
    monkeypatch.setattr(terminal_notify, "_read_console_title", lambda: current[0])
    def _write(title):
        current[0] = title
        return True

    monkeypatch.setattr(terminal_notify, "_write_console_title", _write)

    lease = terminal_notify.begin_title_attention()

    assert lease is not None
    assert current[0] == "🔔 Hermes · cadence"

    terminal_notify.end_title_attention(lease)

    assert current[0] == "Hermes · cadence"


def test_failed_blink_thread_start_restores_title_and_returns_no_lease(monkeypatch):
    current = ["Hermes · cadence"]
    monkeypatch.setattr(terminal_notify, "_title_attention_depth", 0, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_leases", set(), raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_original", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_stop", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_thread", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_read_console_title", lambda: current[0])
    monkeypatch.setattr(
        terminal_notify, "_write_console_title",
        lambda title: current.__setitem__(0, title) or True,
    )

    class _FailingThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("no thread slots")

    monkeypatch.setattr(terminal_notify.threading, "Thread", _FailingThread)

    lease = terminal_notify.begin_title_attention()

    assert lease is None
    assert current[0] == "Hermes · cadence"


def test_stale_title_attention_lease_cannot_release_a_new_prompt(monkeypatch):
    current = ["Hermes · cadence"]
    monkeypatch.setattr(terminal_notify, "_title_attention_depth", 0, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_leases", set(), raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_original", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_stop", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_thread", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_ATTENTION_BLINK_SECONDS", 60, raising=False)
    monkeypatch.setattr(terminal_notify, "_read_console_title", lambda: current[0])
    monkeypatch.setattr(
        terminal_notify, "_write_console_title",
        lambda title: current.__setitem__(0, title) or True,
    )

    first = terminal_notify.begin_title_attention()
    terminal_notify.end_title_attention(first)
    second = terminal_notify.begin_title_attention()

    terminal_notify.end_title_attention(first)
    assert current[0] == "🔔 Hermes · cadence"

    terminal_notify.end_title_attention(second)
    assert current[0] == "Hermes · cadence"


def test_title_attention_blinks_until_released(monkeypatch):
    current = ["Hermes · cadence"]
    writes = []
    launched = {}
    monkeypatch.setattr(terminal_notify, "_title_attention_depth", 0, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_leases", set(), raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_original", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_stop", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_thread", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_ATTENTION_BLINK_SECONDS", 0, raising=False)
    monkeypatch.setattr(terminal_notify, "_read_console_title", lambda: current[0])

    def _write(title):
        current[0] = title
        writes.append(title)
        stop = launched.get("args", (None,))[0]
        if stop is not None and len(writes) >= 3:
            stop.set()
        return True

    class _CapturedThread:
        def __init__(self, *, target, args, daemon, name):
            launched.update(target=target, args=args, daemon=daemon, name=name)

        def start(self):
            launched["started"] = True

    monkeypatch.setattr(terminal_notify, "_write_console_title", _write)
    monkeypatch.setattr(terminal_notify.threading, "Thread", _CapturedThread)

    original = terminal_notify.begin_title_attention()

    assert launched["started"] is True
    launched["target"](*launched["args"])
    assert writes[:3] == [
        "🔔 Hermes · cadence",
        "Hermes · cadence",
        "🔔 Hermes · cadence",
    ]

    terminal_notify.end_title_attention(original)
    assert current[0] == "Hermes · cadence"


def test_real_blink_worker_stops_before_final_title_restore_returns(monkeypatch):
    current = ["Hermes · cadence"]
    writes = []
    blinked_twice = terminal_notify.threading.Event()
    monkeypatch.setattr(terminal_notify, "_title_attention_depth", 0, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_leases", set(), raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_original", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_stop", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_thread", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_ATTENTION_BLINK_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(terminal_notify, "_read_console_title", lambda: current[0])

    def _write(title):
        current[0] = title
        writes.append(title)
        if len(writes) >= 3:
            blinked_twice.set()
        return True

    monkeypatch.setattr(terminal_notify, "_write_console_title", _write)

    lease = terminal_notify.begin_title_attention()
    worker = terminal_notify._title_attention_thread
    assert lease is not None
    assert worker is not None
    assert blinked_twice.wait(timeout=2)

    terminal_notify.end_title_attention(lease)
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert current[0] == "Hermes · cadence"
    assert writes[-1] == "Hermes · cadence"


def test_overlapping_title_attention_restores_only_after_the_last_prompt(monkeypatch):
    current = ["Hermes · cadence"]
    monkeypatch.setattr(terminal_notify, "_title_attention_depth", 0, raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_leases", set(), raising=False)
    monkeypatch.setattr(terminal_notify, "_title_attention_original", None, raising=False)
    monkeypatch.setattr(terminal_notify, "_read_console_title", lambda: current[0])

    def _write(title):
        current[0] = title
        return True

    monkeypatch.setattr(terminal_notify, "_write_console_title", _write)

    first = terminal_notify.begin_title_attention()
    second = terminal_notify.begin_title_attention()

    terminal_notify.end_title_attention(first)
    assert current[0] == "🔔 Hermes · cadence"

    terminal_notify.end_title_attention(second)
    assert current[0] == "Hermes · cadence"
