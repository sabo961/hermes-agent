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
import subprocess
import sys
import threading
import time

_C0_AND_DEL = re.compile(r"[\x00-\x1f\x7f]")
_WARP_PROTOCOL_VERSION = 1
# Last Warp release per channel that set WARP_CLI_AGENT_PROTOCOL_VERSION but could not render
# structured payloads (Warp's should-use-structured.sh). Bash compares lexicographically; so do we.
_WARP_LAST_BROKEN = {"stable": "v0.2026.03.25.08.24.stable_05", "preview": "v0.2026.03.25.08.24.preview_05"}
_ATTENTION_MARKER = "🔔 "
_ATTENTION_BLINK_SECONDS = 0.65
_title_attention_lock = threading.Lock()
_title_attention_depth = 0
_title_attention_original: str | None = None
_title_attention_stop: threading.Event | None = None
_title_attention_thread: threading.Thread | None = None
_title_attention_leases: set[object] = set()
_PROMPT_VOICE_QUIET_SECONDS = 60.0
_prompt_voice_lock = threading.Lock()
_prompt_voice_last = 0.0


def speak_prompt_attention(context: str = "", detail: str = "") -> bool:
    """Launch Zec's local ElevenLabs alert once per quiet window; never block the prompt."""
    del context, detail  # Kept in the interface for future concise, non-secret prompt labels.
    global _prompt_voice_last
    from hermes_constants import get_default_hermes_root

    script = get_default_hermes_root() / "bin" / "zec-say.py"
    if not script.is_file():
        return False
    now = time.monotonic()
    with _prompt_voice_lock:
        if now - _prompt_voice_last < _PROMPT_VOICE_QUIET_SECONDS:
            return False
        args = [sys.executable, str(script), "Zec ovdje. Trebam tvoje odobrenje u terminalu."]
        try:
            if os.name == "nt":
                subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | CREATE_NO_WINDOW
                )
            else:
                subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception:
            return False
        _prompt_voice_last = now
        return True


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


def _blink_title_attention(stop: threading.Event, marker: str) -> None:
    """Alternate the saved title and marker until the final prompt owner releases it."""
    marker_visible = True
    while not stop.wait(_ATTENTION_BLINK_SECONDS):
        with _title_attention_lock:
            if stop is not _title_attention_stop or _title_attention_depth <= 0:
                return
            original = _title_attention_original
            if original is None:
                return
            marker_visible = not marker_visible
            _write_console_title(f"{marker}{original}" if marker_visible else original)


def begin_title_attention(marker: str = _ATTENTION_MARKER) -> object | None:
    """Prefix and blink the current console title; return an owner-specific lease."""
    global _title_attention_depth, _title_attention_original
    global _title_attention_stop, _title_attention_thread
    with _title_attention_lock:
        lease = object()
        if _title_attention_depth:
            _title_attention_leases.add(lease)
            _title_attention_depth = len(_title_attention_leases)
            return lease
        original = _read_console_title()
        if original is None or original.startswith(marker):
            return None
        if not _write_console_title(f"{marker}{original}"):
            return None
        stop = threading.Event()
        thread = threading.Thread(
            target=_blink_title_attention,
            args=(stop, marker),
            daemon=True,
            name="hermes-title-attention",
        )
        _title_attention_original = original
        _title_attention_stop = stop
        _title_attention_thread = thread
        _title_attention_leases.add(lease)
        _title_attention_depth = 1
        try:
            thread.start()
        except Exception:
            _title_attention_leases.discard(lease)
            _title_attention_depth = 0
            _title_attention_original = None
            _title_attention_stop = None
            _title_attention_thread = None
            stop.set()
            _write_console_title(original)
            return None
        return lease


def end_title_attention(lease: object | None, marker: str = _ATTENTION_MARKER) -> None:
    """Release one owner lease and restore the saved title after the final owner exits."""
    global _title_attention_depth, _title_attention_original
    global _title_attention_stop, _title_attention_thread
    if lease is None:
        return
    with _title_attention_lock:
        if lease not in _title_attention_leases:
            return
        _title_attention_leases.remove(lease)
        _title_attention_depth = len(_title_attention_leases)
        if _title_attention_depth:
            return
        saved = _title_attention_original
        stop = _title_attention_stop
        _title_attention_original = None
        _title_attention_stop = None
        _title_attention_thread = None
        if stop is not None:
            stop.set()
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
