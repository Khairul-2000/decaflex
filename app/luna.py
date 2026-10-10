"""Luna: the conversation service. Builds the prompt, calls the model through
LangChain (OpenRouter, OpenAI-compatible) and keeps short-term history."""

import asyncio
import logging
import weakref
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.guards import (
    CRISIS_NOTE,
    FALLBACK_REPLY,
    HONESTY_FALLBACK,
    MINOR_FOLLOWUP,
    MINOR_REPLY,
    detect_crisis,
    detect_minor,
    violates_honesty,
)
from app.persona import load_persona
from app.sessions import SessionData, SessionStore

log = logging.getLogger("luna")

HONESTY_NOTE = (
    "REMINDER: You are an AI. Never claim to be human or deny being an AI. "
    "Rewrite your reply in Luna's voice without that claim."
)


def build_llm(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.agent_api_key or "missing-key",
        base_url=settings.llm_base_url,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        timeout=60,
        max_retries=2,
        default_headers={
            "HTTP-Referer": settings.app_url,
            "X-Title": "Luna",
        },
    )


def _chunk_text(chunk) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    # some providers return a list of content parts
    return "".join(
        part.get("text", "") if isinstance(part, dict) else str(part)
        for part in content
    )


class LunaService:
    def __init__(self, settings: Settings, store: SessionStore, llm=None, persona: str | None = None):
        self.settings = settings
        self.store = store
        self.llm = llm or build_llm(settings)
        self.persona = persona or load_persona(settings.persona_path)
        self._locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = (
            weakref.WeakValueDictionary()
        )

    # ---- helpers -------------------------------------------------------
    def _lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def is_adult(self, session_id: str) -> bool:
        data = await self.store.get(session_id)
        return data.adult and not data.minor

    async def confirm_adult(self, session_id: str) -> None:
        data = await self.store.get(session_id)
        if not data.minor:
            data.adult = True
            await self.store.save(session_id, data)

    async def reset(self, session_id: str) -> None:
        await self.store.clear_history(session_id)

    def _build_messages(
        self, data: SessionData, text: str, extra_note: str | None = None
    ) -> list[BaseMessage]:
        system = self.persona
        if detect_crisis(text):
            system += "\n\n" + CRISIS_NOTE
        if extra_note:
            system += "\n\n" + extra_note
        messages: list[BaseMessage] = [SystemMessage(content=system)]
        window = data.history[-self.settings.history_turns * 2 :]
        for turn in window:
            if turn["role"] == "user":
                messages.append(HumanMessage(content=turn["content"]))
            else:
                messages.append(AIMessage(content=turn["content"]))
        messages.append(HumanMessage(content=text))
        return messages

    async def _commit(self, session_id: str, data: SessionData, text: str, reply: str) -> None:
        data.history.append({"role": "user", "content": text})
        data.history.append({"role": "assistant", "content": reply})
        limit = self.settings.history_turns * 2
        data.history = data.history[-limit:]
        await self.store.save(session_id, data)

    async def _minor_gate(self, session_id: str, data: SessionData, text: str) -> str | None:
        if data.minor:
            return MINOR_FOLLOWUP
        if detect_minor(text):
            data.minor = True
            data.adult = False
            await self.store.save(session_id, data)
            return MINOR_REPLY
        return None

    # ---- public API ----------------------------------------------------
    async def stream(self, session_id: str, text: str) -> AsyncIterator[str]:
        """Yield the reply in chunks (used by the web chat)."""
        text = text.strip()[: self.settings.max_input_chars]
        async with self._lock(session_id):
            data = await self.store.get(session_id)
            fixed = await self._minor_gate(session_id, data, text)
            if fixed:
                yield fixed
                return

            messages = self._build_messages(data, text)
            parts: list[str] = []
            try:
                async for chunk in self.llm.astream(messages):
                    piece = _chunk_text(chunk)
                    if piece:
                        parts.append(piece)
                        yield piece
            except Exception:
                log.exception("LLM streaming failed")
                if not parts:
                    yield FALLBACK_REPLY
                    return

            reply = "".join(parts).strip()
            if not reply:
                yield FALLBACK_REPLY
                return
            if violates_honesty(reply):
                # Already streamed; log it and keep it out of the history so
                # the model doesn't learn from it.
                log.warning("Honesty guard tripped on streamed reply")
                reply = HONESTY_FALLBACK
            await self._commit(session_id, data, text, reply)

    async def reply(self, session_id: str, text: str) -> str:
        """Return the full reply (used by Telegram), with the output guard."""
        text = text.strip()[: self.settings.max_input_chars]
        async with self._lock(session_id):
            data = await self.store.get(session_id)
            fixed = await self._minor_gate(session_id, data, text)
            if fixed:
                return fixed

            reply = await self._generate(self._build_messages(data, text))
            if reply and violates_honesty(reply):
                log.warning("Honesty guard tripped, regenerating once")
                reply = await self._generate(
                    self._build_messages(data, text, extra_note=HONESTY_NOTE)
                )
                if reply and violates_honesty(reply):
                    reply = HONESTY_FALLBACK

            if not reply:
                return FALLBACK_REPLY
            await self._commit(session_id, data, text, reply)
            return reply

    async def _generate(self, messages: list[BaseMessage]) -> str:
        try:
            result = await self.llm.ainvoke(messages)
            return _chunk_text(result).strip()
        except Exception:
            log.exception("LLM call failed")
            return ""
