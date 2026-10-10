import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import chat, telegram as telegram_api
from app.config import BASE_DIR, Settings, get_settings
from app.luna import LunaService
from app.ratelimit import RateLimiter
from app.sessions import create_store
from app.telegram import TelegramBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(
    settings: Settings | None = None,
    luna: LunaService | None = None,
    start_telegram: bool = True,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = None
        if luna is None:
            store = create_store(settings.redis_url, settings.session_ttl_seconds)
            app.state.luna = LunaService(settings, store)
        else:
            app.state.luna = luna
        app.state.settings = settings
        app.state.limiter = RateLimiter(
            settings.rate_limit_per_minute, settings.daily_message_cap
        )

        bot = None
        if start_telegram and settings.telegram_bot_token:
            bot = TelegramBot(settings, app.state.luna, app.state.limiter)
            app.state.telegram = bot
            await bot.start()

        if not settings.agent_api_key:
            logging.getLogger("luna").warning(
                "ARGENTURA_AGENT_API_KEY is empty: model calls will fail until it is set"
            )
        yield

        if bot:
            await bot.stop()
        if store:
            await store.close()

    app = FastAPI(title="Luna", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "model": settings.agent_model,
            "telegram": bool(settings.telegram_bot_token),
        }

    app.include_router(chat.router)
    app.include_router(telegram_api.router)

    static_dir = Path(BASE_DIR) / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
