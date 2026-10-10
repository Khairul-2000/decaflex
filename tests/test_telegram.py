import asyncio

from app.ratelimit import RateLimiter
from app.telegram import TelegramBot


class RecordingBot(TelegramBot):
    """Records Bot API calls instead of hitting the network."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sent: list[tuple[str, dict]] = []

    async def call(self, method, **payload):
        self.sent.append((method, payload))
        return {"ok": True, "result": []}

    def texts(self):
        return [p["text"] for m, p in self.sent if m == "sendMessage"]


def msg(text, chat_id=42, chat_type="private"):
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": chat_type}, "text": text}}


def make_bot(settings, luna):
    return RecordingBot(settings, luna, RateLimiter(100, 1000))


def test_start_shows_age_gate_then_chat_works(settings, make_luna):
    luna, llm = make_luna(["Hey you 😏"])

    async def run():
        bot = make_bot(settings, luna)
        await bot.handle_update(msg("/start"))
        assert "18 or older" in bot.texts()[0]

        # chatting before confirming just re-shows the gate
        await bot.handle_update(msg("hello"))
        assert llm.calls == []

        await bot.handle_update(
            {"callback_query": {"id": "cb1", "data": "age_ok", "message": {"chat": {"id": 42}}}}
        )
        await bot.handle_update(msg("hello"))
        assert bot.texts()[-1] == "Hey you 😏"

    asyncio.run(run())


def test_minor_in_telegram_is_stopped(settings, make_luna):
    luna, llm = make_luna()

    async def run():
        bot = make_bot(settings, luna)
        await luna.confirm_adult("tg:7")
        await bot.handle_update(msg("im 14 years old", chat_id=7))
        assert "adults" in bot.texts()[-1]
        assert llm.calls == []

    asyncio.run(run())


def test_groups_are_ignored(settings, make_luna):
    luna, llm = make_luna()

    async def run():
        bot = make_bot(settings, luna)
        await bot.handle_update(msg("hi", chat_type="group"))
        assert bot.sent == [] and llm.calls == []

    asyncio.run(run())


def test_reset_command(settings, make_luna):
    luna, llm = make_luna(["one", "two"])

    async def run():
        bot = make_bot(settings, luna)
        await luna.confirm_adult("tg:42")
        await bot.handle_update(msg("first"))
        await bot.handle_update(msg("/reset"))
        await bot.handle_update(msg("second"))
        assert len(llm.calls[1]) == 2  # history was wiped

    asyncio.run(run())


def test_honesty_guard_regenerates(settings, make_luna):
    luna, llm = make_luna(["I'm a real girl, not an AI", "I'm an AI, but I'm all yours tonight 🌙"])

    async def run():
        bot = make_bot(settings, luna)
        await luna.confirm_adult("tg:42")
        await bot.handle_update(msg("are you real?"))
        assert bot.texts()[-1].startswith("I'm an AI")
        assert len(llm.calls) == 2

    asyncio.run(run())


def test_webhook_secret_enforced_and_update_processed(make_luna):
    import time

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    s = Settings(_env_file=None, agent_api_key="k", telegram_webhook_secret="s3cret")
    luna, _ = make_luna(["hi there"])
    app = create_app(s, luna=luna, start_telegram=False)
    with TestClient(app) as c:
        # without a bot configured the route is disabled
        assert c.post("/telegram/webhook", json={}).status_code == 404

        bot = RecordingBot(s, luna, RateLimiter(100, 1000))
        app.state.telegram = bot

        bad = c.post("/telegram/webhook", json=msg("/start"))
        assert bad.status_code == 403

        good = c.post(
            "/telegram/webhook",
            json=msg("/start"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        )
        assert good.status_code == 200
        time.sleep(0.3)  # update is processed in the background
        assert any("18 or older" in t for t in bot.texts())
