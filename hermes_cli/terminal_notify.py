"""Terminal-native desktop notifications: OSC 9 and Warp's OSC 777 CLI-agent protocol.

OSC 9 (``ESC ] 9 ; <body> BEL``): Ghostty, iTerm2, Kitty and WezTerm raise an OS notification;
others drop it. OSC 777 (``ESC ] 777 ; notify ; warp://cli-agent ; <json> BEL``): Warp's
structured CLI-agent protocol (tab status + notification mailbox).

Inside the running CLI, ``HermesCLI._ring_bell`` sends ``notification_sequence()`` through the
prompt_toolkit output on the app loop (a second writer on the tty would splice into an in-flight kitty
pet frame). ``write_tty`` is the no-app path: ``/dev/tty`` because ``patch_stdout``'s wrapper strips raw
escapes, falling back to ``sys.stdout`` when ``/dev/tty`` can't be opened (Windows, no controlling
terminal). Never raises.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading

_C0_AND_DEL = re.compile(r"[\x00-\x1f\x7f]")
_WARP_PROTOCOL_VERSION = 1
# Last Warp release per channel that set WARP_CLI_AGENT_PROTOCOL_VERSION but could not render
# structured payloads (Warp's should-use-structured.sh). Bash compares lexicographically; so do we.
_WARP_LAST_BROKEN = {"stable": "v0.2026.03.25.08.24.stable_05", "preview": "v0.2026.03.25.08.24.preview_05"}
_ATTENTION_MARKER = "🔔 "
_title_attention_lock = threading.Lock()
_title_attention_depth = 0
_title_attention_original: str | None = None


def _read_console_title() -> str | None:
    """Return the live Windows console title, or None when unavailable."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetConsoleTitleW(buffer, len(buffer))
        return buffer.value
    except Exception:
        return None


def _write_console_title(title: str) -> bool:
    """Set the live Windows console title without raising."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.SetConsoleTitleW(title))
    except Exception:
        return False


def begin_title_attention(marker: str = _ATTENTION_MARKER) -> str | None:
    """Prefix the current console title and return the exact title to restore."""
    global _title_attention_depth, _title_attention_original
    with _title_attention_lock:
        if _title_attention_depth:
            _title_attention_depth += 1
            return _title_attention_original
        original = _read_console_title()
        if original is None or original.startswith(marker):
            return None
        if not _write_console_title(f"{marker}{original}"):
            return None
        _title_attention_original = original
        _title_attention_depth = 1
        return original


def end_title_attention(original: str | None, marker: str = _ATTENTION_MARKER) -> None:
    """Release one prompt owner and restore the exact saved title after the final owner exits."""
    global _title_attention_depth, _title_attention_original
    if original is None:
        return
    with _title_attention_lock:
        if _title_attention_depth <= 0:
            return
        _title_attention_depth -= 1
        if _title_attention_depth:
            return
        saved = _title_attention_original
        _title_attention_original = None
        if saved is not None:
            _write_console_title(saved)


def write_tty(seq: str) -> None:
    """Write raw escapes to /dev/tty, falling back to sys.stdout. Never raises."""
    try:
        with open("/dev/tty", "w", encoding="utf-8") as tty:
            tty.write(seq)
        return
    except OSError:
        pass
    try:
        sys.stdout.write(seq)
        sys.stdout.flush()
    except Exception:
        pass


def osc9(body: str) -> str:
    """OSC 9 sequence with C0 controls and DEL stripped from the body."""
    return f"\x1b]9;{_C0_AND_DEL.sub('', body)}\x07"


def warp_supported(env=None) -> bool:
    """True when running in a Warp build that can render OSC 777 agent payloads."""
    env = os.environ if env is None else env
    client = env.get("WARP_CLIENT_VERSION", "")
    if env.get("TERM_PROGRAM") != "WarpTerminal" or not env.get("WARP_CLI_AGENT_PROTOCOL_VERSION") or not client:
        return False
    return not any(channel in client and client <= last_broken for channel, last_broken in _WARP_LAST_BROKEN.items())


def warp_osc777(event: str, detail: str, session_id: str = "") -> str:
    """OSC 777 ``warp://cli-agent`` notification; ``event`` is ``stop`` or ``permission_request``."""
    try:
        advertised = int(os.environ.get("WARP_CLI_AGENT_PROTOCOL_VERSION", "1"))
    except ValueError:
        advertised = 1
    cwd = os.getcwd()
    payload = {"v": min(advertised, _WARP_PROTOCOL_VERSION), "agent": "hermes", "event": event,
               "session_id": session_id, "cwd": cwd, "project": os.path.basename(cwd)}
    payload["summary" if event == "permission_request" else "response"] = detail[:200]
    return f"\x1b]777;notify;warp://cli-agent;{json.dumps(payload, separators=(',', ':'))}\x07"


def notification_sequence(context: str, *, prompt: bool, session_id: str = "", detail: str = "") -> str:
    """OSC 9 (plus Warp OSC 777 when supported) for a blocking prompt or turn end."""
    seq = osc9(f"Hermes: {context}")
    if warp_supported():
        event = "permission_request" if prompt else "stop"
        seq += warp_osc777(event, detail or context, session_id)
    return seq
