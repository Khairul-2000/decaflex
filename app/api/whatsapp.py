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
    lady_name: str | None = Field(default=None, description="Assigned Lady Name")
    agency_id: str | None = Field(default=None, description="Assigned Agency ID")
    lady_context: dict[str, Any] | None = Field(default=None, description="Real profile and pricing context from Argentura")
    availability_context: dict[str, Any] | None = Field(default=None, description="Pre-calculated 14-day slot availability schedule from Argentura")
    conversation_state: str | None = Field(default=None, description="Conversation state e.g. AI_ACTIVE")
    ownership_kind: str | None = Field(default=None, description="Ownership kind e.g. ai or operator")
    chat_id: str = Field(description="Argentura conversation / chat ID")
    sender_phone: str = Field(description="Customer phone number (E.164)")
    sender_name: str | None = Field(default="Guest", description="Customer profile name")
    message: str = Field(description="Text message content")
    dispatch: bool = Field(default=False, description="Whether to dispatch message immediately via Argentura HTTP API")



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
    lady_context: dict[str, Any] | None = None,
    lady_name: str | None = None,
    availability_context: dict[str, Any] | None = None,
) -> AgentService:
    # If lady_context was supplied in webhook, build agent with real prices & profile
    if lady_context and isinstance(lady_context, dict):
        lady = LadyProfile.from_lady_context(
            lady_id=lady_id,
            lady_context=lady_context,
            fallback_name=lady_name or settings.argentura_lady_name or "Lila",
        )
        agent = AgentService(
            settings=settings,
            store=store,
            lady=lady,
            client=client,
            availability_context=availability_context,
        )
        _AGENTS[lady_id] = agent
        return agent

    if lady_id in _AGENTS:
        if availability_context:
            _AGENTS[lady_id].availability_context = availability_context
            _AGENTS[lady_id].toolkit.availability_context = availability_context
        return _AGENTS[lady_id]

    lady = await client.get_lady_profile(lady_id)
    if not lady:
        target_name = lady_name or settings.argentura_lady_name or "Lila"
        lady = LadyProfile(id=lady_id, name=target_name)

    agent = AgentService(
        settings=settings,
        store=store,
        lady=lady,
        client=client,
        availability_context=availability_context,
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
    if payload.chat_id.startswith("wa-conversation-"):
        # Greenfield Baileys worker enforces AI_ACTIVE at DB lease claim.
        # Bypass cross-tenant REST lookups that return 404 for non-Test99 agencies.
        if (payload.ownership_kind and payload.ownership_kind.lower() in ("operator", "human")) or \
           (payload.conversation_state and payload.conversation_state.upper() in ("HUMAN_TAKEOVER", "OPERATOR")):
            log.info("Chat %s has operator takeover flag set. AI ignoring.", payload.chat_id)
            return {"status": "ignored", "reason": "operator_takeover"}
    else:
        mode = await client.get_chat_mode(payload.chat_id)
        if mode == "human":
            log.info("Chat %s is in human mode. Operator has taken over; AI ignoring.", payload.chat_id)
            return {"status": "ignored", "reason": "operator_takeover"}

    # 2. Get agent instance for this lady dynamically
    agent = await get_or_create_agent(
        lady_id=lady_id,
        client=client,
        settings=settings,
        store=store,
        lady_context=payload.lady_context,
        lady_name=payload.lady_name,
        availability_context=payload.availability_context,
    )

    session_id = f"wa:{lady_id}:{payload.sender_phone}"
    log.info(
        "Processing WhatsApp message from %s for lady %s '%s' (Chat %s, Agency %s)",
        payload.sender_phone, lady_id, agent.lady.name, payload.chat_id, payload.agency_id
    )

    # 3. Generate response with tools
    reply = await agent.reply(
        session_id=session_id,
        text=payload.message,
        chat_id=payload.chat_id,
    )

    if not reply:
        return {"status": "silent", "reason": "no_reply_or_takeover"}

    sent = False
    if payload.dispatch:
        # Human-like typing delay (jitter) to prevent WhatsApp bans
        delay = random.uniform(1.5, 3.0)
        await asyncio.sleep(delay)
        sent = await client.send_chat_message(payload.chat_id, reply)
        log.info("Dispatched WhatsApp reply to %s: %s (sent: %s)", payload.chat_id, reply[:60], sent)

    return {
        "status": "sent" if sent else "ready",
        "reply": reply,
        "chat_id": payload.chat_id,
    }

