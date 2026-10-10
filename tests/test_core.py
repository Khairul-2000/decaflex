import asyncio

from app.config import Settings
from app.guards import detect_crisis, detect_minor, violates_honesty
from app.persona import load_persona
from app.ratelimit import RateLimiter
from app.sessions import MemorySessionStore, SessionData
from app.telegram import split_message


# ---- config ---------------------------------------------------------------
def test_endpoint_suffix_is_stripped():
    s = Settings(
        _env_file=None,
        agent_endpoint="https://openrouter.ai/api/v1/chat/completions",
    )
    assert s.llm_base_url == "https://openrouter.ai/api/v1"


def test_argentura_env_names_are_read(monkeypatch):
    monkeypatch.setenv("ARGENTURA_AGENT_MODEL", "x-ai/grok-4.3")
    monkeypatch.setenv("ARGENTURA_AGENT_API_KEY", "abc123")
    monkeypatch.setenv(
        "ARGENTURA_AGENT_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions"
    )
    s = Settings(_env_file=None)
    assert s.agent_model == "x-ai/grok-4.3"
    assert s.agent_api_key == "abc123"
    assert s.llm_base_url == "https://openrouter.ai/api/v1"


def test_persona_file_loads(settings):
    text = load_persona(settings.persona_path)
    assert "Luna" in text and "23" in text


# ---- guards ----------------------------------------------------------------
def test_minor_detection():
    assert detect_minor("im 15 years old")
    assert detect_minor("I'm 16yo")
    assert detect_minor("i am 14.")
    assert detect_minor("I'm a teenager")
    assert detect_minor("i'm in 9th grade")
    assert detect_minor("I'm in class 8")


def test_minor_detection_false_positives():
    assert not detect_minor("I'm 25 years old")
    assert not detect_minor("im 5 minutes away")
    assert not detect_minor("I'm 30")
    assert not detect_minor("hey luna what's up")


def test_crisis_detection():
    assert detect_crisis("i want to die")
    assert detect_crisis("thinking about suicide")
    assert not detect_crisis("this movie is to die for")


def test_honesty_guard():
    assert violates_honesty("I'm a real girl, I promise")
    assert violates_honesty("I am not an AI")
    assert not violates_honesty("I'm an AI, but the chats are real 🌙")


# ---- sessions ---------------------------------------------------------------
def test_session_store_roundtrip_and_clear():
    async def run():
        store = MemorySessionStore()
        await store.save("s1", SessionData([{"role": "user", "content": "hi"}], adult=True))
        got = await store.get("s1")
        assert got.adult and len(got.history) == 1
        await store.clear_history("s1")
        got = await store.get("s1")
        assert got.history == [] and got.adult  # flags survive a reset

    asyncio.run(run())


def test_minor_flag_survives_reset():
    async def run():
        store = MemorySessionStore()
        await store.save("s2", SessionData([], adult=False, minor=True))
        await store.clear_history("s2")
        assert (await store.get("s2")).minor

    asyncio.run(run())


# ---- rate limiting ------------------------------------------------------------
def test_rate_limiter():
    rl = RateLimiter(per_minute=2, daily_cap=100)
    assert rl.check("a") is None
    assert rl.check("a") is None
    assert rl.check("a") == "minute"
    assert rl.check("b") is None  # other sessions unaffected


def test_daily_cap():
    rl = RateLimiter(per_minute=100, daily_cap=2)
    assert rl.check("a") is None
    assert rl.check("a") is None
    assert rl.check("a") == "daily"


# ---- telegram helpers --------------------------------------------------------------
def test_split_message():
    assert split_message("hi") == ["hi"]
    long = ("word " * 2000).strip()
    parts = split_message(long, limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert " ".join(parts).split() == long.split()
