"""Nonblocking weekly account-capacity cache for classic CLI consumers.

The cache owns provider polling and returns structured account capacity; footer and
future delegation-selection code consume that data without invoking provider APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import re
import threading
import time
from typing import Callable, Mapping, Optional

from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow, fetch_account_usage


# Public account identities stay provider-neutral; fetch-provider identifiers are internal.
_ACCOUNT_FETCH_PROVIDERS = (("anthropic", "anthropic"), ("openai", "openai-codex"))
_WEEKLY_LABEL_RE = re.compile(r"\b7\s*(?:d|day|days)\b")


@dataclass(frozen=True)
class AccountUsageCapacity:
    """A provider account's weekly capacity, independent of any CLI rendering."""

    provider: str
    weekly_used_percent: float
    reset_at: Optional[datetime]
    fetched_at: datetime
    freshness: str


def select_weekly_usage_window(snapshot: Optional[AccountUsageSnapshot]) -> Optional[AccountUsageWindow]:
    """Select the weekly/7-day window across Anthropic and OpenAI label variants."""
    for window in getattr(snapshot, "windows", ()):
        label = re.sub(r"[-_]+", " ", str(getattr(window, "label", "") or "").casefold())
        if "weekly" in label or "current week" in label or _WEEKLY_LABEL_RE.search(label):
            return window
    return None


class AccountUsageCapacityCache:
    """Stale-while-revalidate capacity cache; rendering never waits for fetches."""

    def __init__(
        self,
        *,
        fetcher: Callable[[str], Optional[AccountUsageSnapshot]] = fetch_account_usage,
        refresh_interval_s: float = 180.0,
        max_stale_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        thread_factory: Optional[Callable[..., threading.Thread]] = None,
    ) -> None:
        self._fetcher = fetcher
        self._refresh_interval_s = refresh_interval_s
        self._max_stale_s = max_stale_s
        self._clock = clock
        self._thread_factory = thread_factory or threading.Thread
        self._entries: dict[str, tuple[AccountUsageCapacity, float]] = {}
        self._lock = threading.Lock()
        self._last_refresh_started = float("-inf")
        self._refresh_thread: Optional[threading.Thread] = None

    def get_capacities(self) -> Mapping[str, AccountUsageCapacity]:
        """Return cheap structured data, retaining successful values only while bounded-stale."""
        now = self._clock()
        with self._lock:
            result = {}
            for provider, (capacity, received_at) in self._entries.items():
                age = now - received_at
                if age <= self._max_stale_s:
                    freshness = "fresh" if age <= self._refresh_interval_s else "stale"
                    result[provider] = replace(capacity, freshness=freshness)
            return result

    def request_refresh(self, invalidate: Callable[[], None]) -> bool:
        """Start at most one daemon refresh per interval; returns immediately without I/O."""
        now = self._clock()
        with self._lock:
            if self._refresh_thread is not None and self._refresh_thread.is_alive():
                return False
            if now - self._last_refresh_started < self._refresh_interval_s:
                return False
            self._last_refresh_started = now
            thread = self._thread_factory(
                target=self._refresh, args=(invalidate,), name="account-usage-refresh", daemon=True)
            self._refresh_thread = thread
            thread.start()
        return True

    def wait_for_refresh(self, timeout: Optional[float] = None) -> bool:
        """Test-only join helper; production callers must use :meth:`get_capacities`."""
        with self._lock:
            thread = self._refresh_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _refresh(self, invalidate: Callable[[], None]) -> None:
        fresh: dict[str, AccountUsageCapacity] = {}
        for account_provider, fetch_provider in _ACCOUNT_FETCH_PROVIDERS:
            try:
                snapshot = self._fetcher(fetch_provider)
                window = select_weekly_usage_window(snapshot)
                used_percent = getattr(window, "used_percent", None)
                if window is None or used_percent is None:
                    continue
                used = max(0.0, min(100.0, float(used_percent)))
                fresh[account_provider] = AccountUsageCapacity(
                    provider=account_provider,
                    weekly_used_percent=used,
                    reset_at=getattr(window, "reset_at", None),
                    fetched_at=getattr(snapshot, "fetched_at", None) or datetime.now().astimezone(),
                    freshness="fresh",
                )
            except Exception:
                continue
        now = self._clock()
        if fresh:
            with self._lock:
                self._entries.update({provider: (capacity, now) for provider, capacity in fresh.items()})
        try:
            invalidate()
        except Exception:
            pass
