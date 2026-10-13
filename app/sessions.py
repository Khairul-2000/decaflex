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


class SessionStore(Protocol):
    async def get(self, session_id: str) -> SessionData: ...
    async def save(self, session_id: str, data: SessionData) -> None: ...
    async def clear_history(self, session_id: str) -> None: ...
    async def close(self) -> None: ...


class MemorySessionStore:
    def __init__(self, ttl_seconds: int = 86400, max_sessions: int = 10000):
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
        return SessionData(list(data.history), data.adult, data.minor)

    async def save(self, session_id: str, data: SessionData) -> None:
        self._data[session_id] = (
            time.monotonic(),
            SessionData(list(data.history), data.adult, data.minor),
        )
        self._purge()

    async def clear_history(self, session_id: str) -> None:
        entry = self._data.get(session_id)
        if entry:
            data = entry[1]
            await self.save(session_id, SessionData([], data.adult, data.minor))

    async def close(self) -> None:
        self._data.clear()


class RedisSessionStore:
    def __init__(self, url: str, ttl_seconds: int = 86400):
        import redis.asyncio as redis

        self._redis = redis.from_url(url, decode_responses=True)
        self._ttl = ttl_seconds

    @staticmethod
    def _key(session_id: str) -> str:
        return f"luna:session:{session_id}"

    async def get(self, session_id: str) -> SessionData:
        raw = await self._redis.get(self._key(session_id))
        if not raw:
            return SessionData()
        obj = json.loads(raw)
        return SessionData(
            history=obj.get("history", []),
            adult=obj.get("adult", False),
            minor=obj.get("minor", False),
        )

    async def save(self, session_id: str, data: SessionData) -> None:
        await self._redis.set(
            self._key(session_id), json.dumps(asdict(data)), ex=self._ttl
        )

    async def clear_history(self, session_id: str) -> None:
        data = await self.get(session_id)
        data.history = []
        await self.save(session_id, data)

    async def close(self) -> None:
        await self._redis.aclose()


def create_store(redis_url: str, ttl_seconds: int) -> SessionStore:
    if redis_url:
        return RedisSessionStore(redis_url, ttl_seconds)
    return MemorySessionStore(ttl_seconds)


_default_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _default_store
    if _default_store is None:
        _default_store = MemorySessionStore()
    return _default_store
