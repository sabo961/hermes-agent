"""Display-only timestamp helpers shared by classic CLI rendering paths."""

from __future__ import annotations

from datetime import datetime


def resolve_response_timestamp_position(value) -> str:
    """Normalize the classic-CLI assistant timestamp placement."""
    position = str(value or "label").strip().lower()
    return position if position in {"label", "footer", "both"} else "label"


def assistant_timestamp_at(cli, position: str) -> bool:
    """Return whether assistant chrome should show a timestamp at ``position``."""
    configured = resolve_response_timestamp_position(
        getattr(cli, "response_timestamp_position", "label")
    )
    return bool(getattr(cli, "show_timestamps", False)) and configured in {position, "both"}


def formatted_response_timestamp(cli) -> str:
    """Format local completion time with an optional unambiguous UTC-offset placeholder."""
    stamp = datetime.now()
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    template = getattr(cli, "timestamp_format", "%H:%M")
    offset = stamp.strftime("%z")
    if len(offset) == 5 and offset[0] in "+-":
        offset = f"{offset[:3]}:{offset[3:]}"
    return stamp.strftime(template.replace("{utc_offset}", offset))
