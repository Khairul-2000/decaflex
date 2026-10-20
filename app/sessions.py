"""Short-term conversation state. Nothing here touches Postgres.

Two backends: in-memory (default, lost on restart) and Redis (optional,
survives restarts, expires via TTL)."""

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass
class SessionData:
    history: list[dict] = field(default_factory=list)  # [{"role": "user"|"assistant", "content": str}]
    adult: bool = False
    minor: bool = False
    last_active: float = field(default_factory=time.time)


class SessionStore(Protocol):
    async def get(self, session_id: str) -> SessionData: ...
    async def save(self, session_id: str, data: SessionData) -> None: ...
    async def clear_history(self, session_id: str) -> None: ...
    async def close(self) -> None: ...


class MemorySessionStore:
    def __init__(self, ttl_seconds: int = 21600, max_sessions: int = 10000):
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._data: dict[str, tuple[float, SessionData]] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._data.items() if now - ts > self._ttl]
        for key in expired:
            del self._data[key]
        if len(self._data) > self._max:
            oldest = sorted(self._data.items(), key=lambda kv: kv[1][0])
            for key, _ in oldest[: len(self._data) - self._max]:
                del self._data[key]

    async def get(self, session_id: str) -> SessionData:
        self._purge()
        entry = self._data.get(session_id)
        if entry is None:
            return SessionData()
        # return a copy so callers can't mutate stored state by accident
        data = entry[1]
        if time.time() - data.last_active > self._ttl:
            return SessionData(adult=data.adult, minor=data.minor, last_active=time.time())
        return SessionData(list(data.history), data.adult, data.minor, data.last_active)

    async def save(self, session_id: str, data: SessionData) -> None:
        data.last_active = time.time()
        self._data[session_id] = (
            time.monotonic(),
            SessionData(list(data.history), data.adult, data.minor, data.last_active),
        )
        self._purge()

    async def clear_history(self, session_id: str) -> None:
        entry = self._data.get(session_id)
        if entry:
            data = entry[1]
            await self.save(session_id, SessionData([], data.adult, data.minor, time.time()))

    async def close(self) -> None:
        self._data.clear()


import logging

log = logging.getLogger("luna.sessions")


class RedisSessionStore:
    def __init__(self, url: str, ttl_seconds: int = 21600):
        import redis.asyncio as redis

        self._redis = redis.from_url(url, decode_responses=True)
        self._ttl = ttl_seconds

    @staticmethod
    def _key(session_id: str) -> str:
        return f"luna:session:{session_id}"

    async def get(self, session_id: str) -> SessionData:
        key = self._key(session_id)
        raw = await self._redis.get(key)
        log.info("Redis session GET '%s' (found: %s)", key, bool(raw))
        if not raw:
            return SessionData()
        obj = json.loads(raw)
        last_active = obj.get("last_active", time.time())
        # If inactive for more than ttl (6 hours), reset conversation history
        if time.time() - last_active > self._ttl:
            log.info("Session '%s' inactive for %ds (> %ds TTL). Resetting history to start fresh context.", key, int(time.time() - last_active), self._ttl)
            return SessionData(
                history=[],
                adult=obj.get("adult", False),
                minor=obj.get("minor", False),
                last_active=time.time(),
            )
        return SessionData(
            history=obj.get("history", []),
            adult=obj.get("adult", False),
            minor=obj.get("minor", False),
            last_active=last_active,
        )

    async def save(self, session_id: str, data: SessionData) -> None:
        data.last_active = time.time()
        key = self._key(session_id)
        log.info("Redis session SET '%s' (TTL: %ds, messages: %d)", key, self._ttl, len(data.history))
        await self._redis.set(
            key, json.dumps(asdict(data)), ex=self._ttl
        )

    async def clear_history(self, session_id: str) -> None:
        data = await self.get(session_id)
        data.history = []
        await self.save(session_id, data)

    async def close(self) -> None:
        await self._redis.aclose()


_default_store: SessionStore | None = None


def create_store(redis_url: str, ttl_seconds: int) -> SessionStore:
    global _default_store
    if redis_url:
        _default_store = RedisSessionStore(redis_url, ttl_seconds)
    else:
        _default_store = MemorySessionStore(ttl_seconds)
    return _default_store


def get_session_store() -> SessionStore:
    global _default_store
    if _default_store is None:
        from app.config import get_settings
        settings = get_settings()
        _default_store = create_store(settings.redis_url, settings.session_ttl_seconds)
    return _default_store
