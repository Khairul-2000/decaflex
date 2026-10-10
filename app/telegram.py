"""Telegram transport for Luna, talking to the Bot API directly over httpx.
Supports webhook mode (production) and long polling (local dev)."""

import asyncio
import logging

import httpx

from app.config import Settings
from app.guards import DAILY_CAP_REPLY, RATE_LIMIT_REPLY
from app.luna import LunaService
from app.ratelimit import RateLimiter

log = logging.getLogger("luna.telegram")

TG_LIMIT = 4096

AGE_GATE_TEXT = (
    "Hey, I'm Luna 🌙 an AI companion who loves a good flirty chat.\n\n"
    "Before we start: this chat is for adults only. Are you 18 or older?"
)
WELCOME_TEXT = "Perfect 😏 Now that that's out of the way... how's your day going?"
RESET_TEXT = "Fresh start 🌙 What shall we talk about?"
NON_TEXT_TEXT = "I only read text for now 🌙 Tell me in words!"


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Split on paragraph/sentence/space boundaries so each part fits Telegram's limit."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit), text.rfind(". ", 0, limit), text.rfind(" ", 0, limit))
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


class TelegramBot:
    def __init__(
        self,
        settings: Settings,
        luna: LunaService,
        limiter: RateLimiter,
        client: httpx.AsyncClient | None = None,
    ):
        self.settings = settings
        self.luna = luna
        self.limiter = limiter
        self.client = client or httpx.AsyncClient(timeout=40)
        self._api = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self._poll_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---- Bot API calls (overridable in tests) --------------------------
    async def call(self, method: str, **payload):
        resp = await self.client.post(f"{self._api}/{method}", json=payload)
        data = resp.json()
        if not data.get("ok"):
            log.warning("Telegram %s failed: %s", method, data)
        return data

    async def send_text(self, chat_id: int, text: str, **extra) -> None:
        for part in split_message(text):
            await self.call("sendMessage", chat_id=chat_id, text=part, **extra)

    async def send_age_gate(self, chat_id: int) -> None:
        await self.call(
            "sendMessage",
            chat_id=chat_id,
            text=AGE_GATE_TEXT,
            reply_markup={
                "inline_keyboard": [[{"text": "I'm 18 or older ✅", "callback_data": "age_ok"}]]
            },
        )

    # ---- update handling ----------------------------------------------
    @staticmethod
    def session_id(chat_id: int) -> str:
        return f"tg:{chat_id}"

    async def handle_update(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                await self._handle_callback(update["callback_query"])
            elif "message" in update:
                await self._handle_message(update["message"])
        except Exception:
            log.exception("Failed to handle Telegram update")

    async def _handle_callback(self, cb: dict) -> None:
        message = cb.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        await self.call("answerCallbackQuery", callback_query_id=cb.get("id"))
        if chat_id is None or cb.get("data") != "age_ok":
            return
        await self.luna.confirm_adult(self.session_id(chat_id))
        if await self.luna.is_adult(self.session_id(chat_id)):
            await self.send_text(chat_id, WELCOME_TEXT)

    async def _handle_message(self, msg: dict) -> None:
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            return  # private chats only
        chat_id = chat["id"]
        sid = self.session_id(chat_id)
        text = (msg.get("text") or "").strip()

        if not text:
            await self.send_text(chat_id, NON_TEXT_TEXT)
            return

        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if command == "/start":
            if await self.luna.is_adult(sid):
                await self.send_text(chat_id, "Hey you 😏 Missed me already?")
            else:
                await self.send_age_gate(chat_id)
            return
        if command == "/reset":
            await self.luna.reset(sid)
            await self.send_text(chat_id, RESET_TEXT)
            return
        if command:
            return  # ignore other commands

        data = await self.luna.store.get(sid)
        if not data.minor and not data.adult:
            await self.send_age_gate(chat_id)
            return

        blocked = self.limiter.check(sid)
        if blocked:
            await self.send_text(chat_id, DAILY_CAP_REPLY if blocked == "daily" else RATE_LIMIT_REPLY)
            return

        typing = asyncio.create_task(self._keep_typing(chat_id))
        try:
            reply = await self.luna.reply(sid, text)
        finally:
            typing.cancel()
        await self.send_text(chat_id, reply)

    async def _keep_typing(self, chat_id: int) -> None:
        try:
            while True:
                await self.call("sendChatAction", chat_id=chat_id, action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    def spawn(self, update: dict) -> None:
        """Process an update in the background (used by the webhook route)."""
        task = asyncio.create_task(self.handle_update(update))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        if self.settings.telegram_webhook_url:
            payload = {
                "url": self.settings.telegram_webhook_url,
                "allowed_updates": ["message", "callback_query"],
            }
            if self.settings.telegram_webhook_secret:
                payload["secret_token"] = self.settings.telegram_webhook_secret
            await self.call("setWebhook", **payload)
            log.info("Telegram webhook set")
        else:
            await self.call("deleteWebhook")
            self._poll_task = asyncio.create_task(self._poll_forever())
            log.info("Telegram long polling started")

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for task in list(self._tasks):
            task.cancel()
        await self.client.aclose()

    async def _poll_forever(self) -> None:
        offset = 0
        while True:
            try:
                data = await self.call(
                    "getUpdates",
                    offset=offset,
                    timeout=30,
                    allowed_updates=["message", "callback_query"],
                )
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    self.spawn(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Polling error, retrying in 3s")
                await asyncio.sleep(3)
