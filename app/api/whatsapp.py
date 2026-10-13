"""WhatsApp Webhook Handler (Baileys Gateway Integration)."""

import asyncio
import logging
import random
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent_service import AgentService
from app.argentura.client import ArgenturaClient
from app.argentura.models import LadyProfile
from app.config import Settings, get_settings
from app.sessions import SessionStore, get_session_store

log = logging.getLogger("argentura.whatsapp")
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


class IncomingWhatsAppMessage(BaseModel):
    lady_id: str | None = Field(default=None, description="Assigned Lady ID")
    chat_id: str = Field(description="Argentura conversation / chat ID")
    sender_phone: str = Field(description="Customer phone number (E.164)")
    sender_name: str | None = Field(default="Guest", description="Customer profile name")
    message: str = Field(description="Text message content")


# Cache for active agent instances per lady
_AGENTS: dict[str, AgentService] = {}
_CLIENT: ArgenturaClient | None = None


async def get_argentura_client(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ArgenturaClient:
    global _CLIENT
    app_client = getattr(request.app.state, "argentura_client", None)
    if app_client:
        return app_client
    if _CLIENT is None:
        _CLIENT = ArgenturaClient(
            base_url=settings.argentura_base_url,
            username=settings.argentura_username,
            pin=settings.argentura_pin,
        )
        await _CLIENT.login()
    return _CLIENT


async def get_or_create_agent(
    lady_id: str,
    client: ArgenturaClient,
    settings: Settings,
    store: SessionStore,
) -> AgentService:
    if lady_id in _AGENTS:
        return _AGENTS[lady_id]

    lady = await client.get_lady_profile(lady_id)
    if not lady:
        # Fallback to first available lady
        ladies = await client.get_ladies()
        if ladies:
            lady = ladies[0]
        else:
            lady = LadyProfile(id=lady_id, name="Julia")

    agent = AgentService(
        settings=settings,
        store=store,
        lady=lady,
        client=client,
    )
    _AGENTS[lady_id] = agent
    return agent


@router.post("/webhook")
async def handle_incoming_whatsapp(
    payload: IncomingWhatsAppMessage,
    settings: Settings = Depends(get_settings),
    store: SessionStore = Depends(get_session_store),
    client: ArgenturaClient = Depends(get_argentura_client),
):
    """Handle incoming message from Argentura's Baileys WhatsApp integration."""
    lady_id = payload.lady_id
    if not lady_id:
        ladies = await client.get_ladies()
        lady_id = ladies[0].id if ladies else "default"

    # 1. Check if human operator took over this chat
    mode = await client.get_chat_mode(payload.chat_id)
    if mode == "human":
        log.info("Chat %s is in human mode. Operator has taken over; AI ignoring.", payload.chat_id)
        return {"status": "ignored", "reason": "operator_takeover"}

    # 2. Get agent instance for this lady
    agent = await get_or_create_agent(lady_id, client, settings, store)

    session_id = f"wa:{lady_id}:{payload.sender_phone}"
    log.info("Processing WhatsApp message from %s for lady %s (Chat %s)", payload.sender_phone, lady_id, payload.chat_id)

    # 3. Generate response with tools
    reply = await agent.reply(
        session_id=session_id,
        text=payload.message,
        chat_id=payload.chat_id,
    )

    if not reply:
        return {"status": "silent", "reason": "no_reply_or_takeover"}

    # 4. Human-like typing delay (jitter) to prevent WhatsApp bans
    delay = random.uniform(1.5, 3.0)
    await asyncio.sleep(delay)

    # 5. Dispatch reply back to customer via Argentura Baileys bridge
    sent = await client.send_chat_message(payload.chat_id, reply)
    log.info("Dispatched WhatsApp reply to %s: %s (sent: %s)", payload.chat_id, reply[:60], sent)

    return {
        "status": "sent" if sent else "queued",
        "reply": reply,
        "chat_id": payload.chat_id,
    }
