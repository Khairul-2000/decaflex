import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.luna import LunaService  # noqa: E402
from app.sessions import MemorySessionStore  # noqa: E402


class FakeChunk:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """Stands in for ChatOpenAI: scripted replies, records the prompts it saw."""

    def __init__(self, replies=None):
        self.replies = list(replies or ["Hey you 😏 How's your day?"])
        self.calls: list[list] = []

    def _next(self) -> str:
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    async def astream(self, messages):
        self.calls.append(messages)
        for word in self._next().split(" "):
            yield FakeChunk(word + " ")

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return FakeChunk(self._next())


@pytest.fixture
def settings():
    return Settings(_env_file=None, agent_api_key="test-key", rate_limit_per_minute=3)


@pytest.fixture
def make_luna(settings):
    def _make(replies=None):
        llm = FakeLLM(replies)
        return LunaService(settings, MemorySessionStore(), llm=llm), llm

    return _make
