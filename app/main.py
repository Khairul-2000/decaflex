import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    start_argentura: bool | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if start_argentura is None:
        start_argentura = (luna is None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = None
        if luna is None:
            store = create_store(settings.redis_url, settings.session_ttl_seconds)
            app.state.luna = LunaService(settings, store)
        else:
            app.state.luna = luna
            store = getattr(luna, "store", None)
        app.state.store = store
        app.state.settings = settings
        app.state.limiter = RateLimiter(
            settings.rate_limit_per_minute, settings.daily_message_cap
        )

        bot = None
        if start_telegram and settings.telegram_bot_token:
            bot = TelegramBot(settings, app.state.luna, app.state.limiter)
            app.state.telegram = bot
            await bot.start()

        # Initialize live Argentura client
        if start_argentura:
            from app.argentura.client import ArgenturaClient

            arg_client = ArgenturaClient(
                base_url=settings.argentura_base_url,
                username=settings.argentura_username,
                pin=settings.argentura_pin,
            )
            try:
                ok = await arg_client.login()
                if ok:
                    app.state.argentura_client = arg_client
                    logging.getLogger("argentura").info(
                        "✅ Argentura client ACTIVE (Agency: %s, Base: %s)",
                        arg_client.agency_id, arg_client.base_url
                    )
                else:
                    logging.getLogger("argentura").warning("Failed to authenticate with Argentura suite")
            except Exception:
                logging.getLogger("argentura").exception("Could not initialize Argentura client")

        if not settings.agent_api_key:
            logging.getLogger("luna").warning(
                "ARGENTURA_AGENT_API_KEY is empty: model calls will fail until it is set"
            )
        yield

        if bot:
            await bot.stop()
        if store:
            await store.close()

    app = FastAPI(title="Luna-Argentura", version="2.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        store = getattr(app.state, "store", None)
        redis_connected = False
        if store and hasattr(store, "_redis"):
            try:
                await store._redis.ping()
                redis_connected = True
            except Exception:
                redis_connected = False
        return {
            "status": "ok",
            "engine": "multi-tenant-dynamic",
            "model": settings.agent_model,
            "redis_connected": redis_connected,
            "telegram": bool(settings.telegram_bot_token),
        }

    app.include_router(chat.router)
    app.include_router(telegram_api.router)
    from app.api import whatsapp
    app.include_router(whatsapp.router)

    @app.get("/")
    async def root():
        return {
            "name": "Argentura AI Companion Engine",
            "version": "2.0.0",
            "status": "online",
            "endpoints": {
                "health": "/health",
                "whatsapp_webhook": "/whatsapp/webhook",
                "chat": "/chat",
            },
        }

    return app



app = create_app()
