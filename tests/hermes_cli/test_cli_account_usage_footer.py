"""Behavior tests for the classic CLI account-usage footer row."""

from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
from cli import HermesCLI


def _snapshot(provider, label, percent, reset_at):
    return AccountUsageSnapshot(
        provider=provider,
        source="test",
        fetched_at=datetime.now(timezone.utc),
        windows=(AccountUsageWindow(label, percent, reset_at),),
    )


def test_status_bar_exposes_weekly_account_usage_selector():
    """The footer only reports the provider's weekly/7-day usage window."""
    assert hasattr(HermesCLI, "_select_account_usage_weekly_window")


def test_selects_weekly_label_variants_but_not_short_session_limit():
    snapshot = AccountUsageSnapshot(
        provider="anthropic",
        source="test",
        fetched_at=datetime.now(timezone.utc),
        windows=(
            AccountUsageWindow("Current session", 12),
            AccountUsageWindow("Current 7-day", 52),
        ),
    )

    weekly = HermesCLI._select_account_usage_weekly_window(snapshot)

    assert weekly is not None
    assert weekly.label == "Current 7-day"


def test_footer_formats_two_weekly_windows_with_croatian_local_reset_labels():
    class ResetAt:
        def __init__(self, croatian_local, host_local):
            self.croatian_local = croatian_local
            self.host_local = host_local

        def astimezone(self, tz=None):
            return self.croatian_local if tz == ZoneInfo("Europe/Zagreb") else self.host_local

    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj._account_usage_local_timezone = lambda: ZoneInfo("Europe/Zagreb")
    capacities = {
        "anthropic": SimpleNamespace(
            provider="anthropic", weekly_used_percent=52.4,
            reset_at=ResetAt(datetime(2026, 9, 21, 2), datetime(2026, 9, 21, 4)),
        ),
        "openai": SimpleNamespace(
            provider="openai", weekly_used_percent=7.2,
            reset_at=ResetAt(datetime(2026, 9, 27, 10), datetime(2026, 9, 27, 12)),
        ),
    }

    fragments = getattr(cli_obj, "_format_account_usage_footer_fragments", lambda *_: [])(capacities, 80)
    text = "".join(text for _style, text in fragments)

    assert "Anthropic 7d 52% ↻ pon 02h │ OpenAI 7d 7% ↻ ned 10h" in text
    assert cli_obj._status_bar_display_width(text) == 80


def test_narrow_footer_keeps_both_provider_percentages_before_reset_details():
    cli_obj = HermesCLI.__new__(HermesCLI)
    capacities = {
        "anthropic": SimpleNamespace(provider="anthropic", weekly_used_percent=52,
                                       reset_at=datetime(2026, 9, 21, 2, tzinfo=timezone.utc)),
        "openai": SimpleNamespace(provider="openai", weekly_used_percent=7,
                                          reset_at=datetime(2026, 9, 27, 10, tzinfo=timezone.utc)),
    }

    fragments = getattr(cli_obj, "_format_account_usage_footer_fragments", lambda *_: [])(capacities, 42)
    text = "".join(text for _style, text in fragments)

    assert "Anthropic 7d 52%" in text
    assert "OpenAI 7d 7%" in text
    assert "↻" not in text
    assert cli_obj._status_bar_display_width(text) == 42


def test_narrow_footer_preserves_both_percentages_at_compact_width_boundaries():
    cli_obj = HermesCLI.__new__(HermesCLI)
    capacities = {
        "anthropic": SimpleNamespace(provider="anthropic", weekly_used_percent=52, reset_at=None),
        "openai": SimpleNamespace(provider="openai", weekly_used_percent=7, reset_at=None),
    }

    for width in (20, 17, 6):
        text = "".join(text for _style, text in cli_obj._format_account_usage_footer_fragments(capacities, width))

        assert "52%" in text
        assert "7%" in text
        assert cli_obj._status_bar_display_width(text) == width

    too_narrow = "".join(text for _style, text in cli_obj._format_account_usage_footer_fragments(capacities, 5))
    assert cli_obj._status_bar_display_width(too_narrow) == 5


def test_capacity_cache_refreshes_in_daemon_and_exposes_structured_snapshot(monkeypatch):
    from hermes_cli.status_bar_account_usage import AccountUsageCapacityCache

    snapshots = {
        "anthropic": _snapshot("anthropic", "Current week", 59, datetime(2026, 9, 21, 2, tzinfo=timezone.utc)),
        "openai-codex": _snapshot("openai-codex", "Weekly", 7, datetime(2026, 9, 27, 10, tzinfo=timezone.utc)),
    }
    cache = AccountUsageCapacityCache(fetcher=snapshots.get)
    invalidated = MagicMock()

    assert cache.get_capacities() == {}
    assert cache.request_refresh(invalidated) is True
    assert cache.wait_for_refresh(timeout=1.0)

    capacities = cache.get_capacities()
    assert capacities["anthropic"].weekly_used_percent == 59
    assert capacities["anthropic"].reset_at == datetime(2026, 9, 21, 2, tzinfo=timezone.utc)
    assert capacities["anthropic"].freshness == "fresh"
    assert capacities["openai"].weekly_used_percent == 7
    invalidated.assert_called_once_with()


def test_capacity_cache_throttles_refreshes_for_180_seconds_without_sleeping():
    from hermes_cli.status_bar_account_usage import AccountUsageCapacityCache

    class InlineThread:
        def __init__(self, *, target, args=(), **_kwargs):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def is_alive(self):
            return False

        def join(self, _timeout=None):
            return None

    now = [0.0]
    fetcher = MagicMock(return_value=None)
    invalidated = MagicMock()
    cache = AccountUsageCapacityCache(
        fetcher=fetcher,
        clock=lambda: now[0],
        thread_factory=lambda **kwargs: InlineThread(**kwargs),
    )

    assert cache.request_refresh(invalidated) is True
    now[0] = 179.0
    assert cache.request_refresh(invalidated) is False
    now[0] = 180.0
    assert cache.request_refresh(invalidated) is True

    assert fetcher.call_count == 4
    assert invalidated.call_count == 2


def test_capacity_cache_expires_entries_after_900_seconds():
    from hermes_cli.status_bar_account_usage import AccountUsageCapacityCache

    now = [0.0]
    snapshot = _snapshot("anthropic", "Weekly", 52, None)
    cache = AccountUsageCapacityCache(
        fetcher=lambda provider: snapshot if provider == "anthropic" else None,
        clock=lambda: now[0],
    )

    cache._refresh(lambda: None)
    now[0] = 900.0
    assert "anthropic" in cache.get_capacities()
    now[0] = 900.1
    assert cache.get_capacities() == {}


def test_capacity_cache_preserves_failed_provider_while_updating_the_other():
    from hermes_cli.status_bar_account_usage import AccountUsageCapacityCache

    anth_initial = _snapshot("anthropic", "Weekly", 52, None)
    openai_initial = _snapshot("openai-codex", "Weekly", 7, None)
    openai_updated = _snapshot("openai-codex", "Weekly", 8, None)
    responses = [anth_initial, openai_initial, RuntimeError("anthropic unavailable"), openai_updated]

    def fetcher(_provider):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    cache = AccountUsageCapacityCache(fetcher=fetcher, clock=lambda: 0.0)
    cache._refresh(lambda: None)
    cache._refresh(lambda: None)

    capacities = cache.get_capacities()
    assert capacities["anthropic"].weekly_used_percent == 52
    assert capacities["openai"].weekly_used_percent == 8


def test_capacity_cache_invalidates_once_for_each_completed_refresh():
    from hermes_cli.status_bar_account_usage import AccountUsageCapacityCache

    invalidated = MagicMock()
    cache = AccountUsageCapacityCache(fetcher=lambda _provider: None)

    cache._refresh(invalidated)

    invalidated.assert_called_once_with()


def test_status_area_adds_second_line_only_after_capacity_is_available():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj._status_bar_visible = True
    cli_obj._status_bar_suppressed_after_resize = False
    cli_obj._get_status_bar_fragments = lambda: [("class:status-bar", " main ")]
    cli_obj._get_account_usage_capacity_snapshot = lambda: {}

    assert cli_obj._get_status_area_fragments() == [("class:status-bar", " main ")]
    assert cli_obj._status_area_height() == 1

    cli_obj._get_account_usage_capacity_snapshot = lambda: {
        "anthropic": SimpleNamespace(provider="anthropic", weekly_used_percent=59,
                                       reset_at=datetime(2026, 9, 21, 2, tzinfo=timezone.utc))
    }
    cli_obj._get_tui_terminal_width = lambda default=(80, 24): 80
    area = cli_obj._get_status_area_fragments()

    assert "\n" in "".join(text for _style, text in area)
    assert cli_obj._status_area_height() == 2


def test_status_area_content_and_height_share_one_capacity_snapshot_per_render_pass():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj._get_status_bar_fragments = lambda: [("class:status-bar", " main ")]
    cli_obj._get_tui_terminal_width = lambda default=(80, 24): 80
    snapshots = iter(({}, {
        "anthropic": SimpleNamespace(
            provider="anthropic", weekly_used_percent=59,
            reset_at=datetime(2026, 9, 21, 2, tzinfo=timezone.utc),
        ),
    }))
    cli_obj._get_account_usage_capacity_snapshot = lambda: next(snapshots)

    area = cli_obj._get_status_area_fragments()

    assert "\n" not in "".join(text for _style, text in area)
    assert cli_obj._status_area_height() == 1
