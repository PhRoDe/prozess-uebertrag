"""Simple in-memory rate-limiter.

Good enough for an internal tool with a single server process. Keeps a deque
of timestamps per key and rejects when more than `max_hits` have occurred
within `window_seconds`.

Not distributed — if we ever scale to multiple Railway replicas, replace with
a Supabase-backed counter or Redis.
"""
from collections import defaultdict, deque
from threading import Lock
from time import monotonic

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def allow(key: str, max_hits: int, window_seconds: int) -> bool:
    """Return True if the event is within budget, False if it should be rejected."""
    now = monotonic()
    cutoff = now - window_seconds
    with _lock:
        q = _hits[key]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= max_hits:
            return False
        q.append(now)
        return True


def reset(key: str | None = None) -> None:
    """Clear hits for a key (for tests) or all keys if None."""
    with _lock:
        if key is None:
            _hits.clear()
        else:
            _hits.pop(key, None)
