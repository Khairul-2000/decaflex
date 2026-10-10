import time
from collections import defaultdict, deque
from datetime import date


class RateLimiter:
    """Per-session sliding-window (per minute) plus a daily message cap.
    In-memory, so limits are per process."""

    def __init__(self, per_minute: int, daily_cap: int):
        self.per_minute = per_minute
        self.daily_cap = daily_cap
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._daily: dict[str, tuple[date, int]] = {}

    def check(self, session_id: str) -> str | None:
        """Record a hit. Returns None if allowed, else 'minute' or 'daily'."""
        today = date.today()
        day, count = self._daily.get(session_id, (today, 0))
        if day != today:
            day, count = today, 0
        if count >= self.daily_cap:
            return "daily"

        now = time.monotonic()
        window = self._hits[session_id]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return "minute"

        window.append(now)
        self._daily[session_id] = (day, count + 1)
        return None
