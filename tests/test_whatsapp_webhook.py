"""Tests for WhatsApp Webhook integration endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.api.whatsapp import get_argentura_client
from app.argentura.client import ArgenturaClient
from app.argentura.models import LadyProfile
from app.main import app


class MockArgenturaClient(ArgenturaClient):
    def __init__(self):
        super().__init__()
        self.sent_messages = []
        self.mode_map = {}

    async def get_chat_mode(self, chat_id: str) -> str:
        return self.mode_map.get(chat_id, "ai")

    async def get_ladies(self) -> list[LadyProfile]:
        return [LadyProfile(id="lady-julia", name="Julia")]

    async def get_lady_profile(self, lady_id: str) -> LadyProfile | None:
        return LadyProfile(id=lady_id, name="Julia")

    async def send_chat_message(self, chat_id: str, text: str) -> bool:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return True


def test_whatsapp_webhook_operator_takeover():
    mock_client = MockArgenturaClient()
    mock_client.mode_map["chat-123"] = "human"  # Operator active!

    app.dependency_overrides[get_argentura_client] = lambda: mock_client
    client = TestClient(app)

    payload = {
        "lady_id": "lady-julia",
        "chat_id": "chat-123",
        "sender_phone": "+49170111222",
        "message": "Hello, are you free tomorrow?",
    }

    resp = client.post("/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert resp.json()["reason"] == "operator_takeover"
    assert len(mock_client.sent_messages) == 0

    app.dependency_overrides.clear()


def test_whatsapp_webhook_greenfield_conversation_bypass():
    class ThrowingArgenturaClient(MockArgenturaClient):
        async def get_chat_mode(self, chat_id: str) -> str:
            raise RuntimeError("Should not be called for wa-conversation-* internal chats")

    mock_client = ThrowingArgenturaClient()
    app.dependency_overrides[get_argentura_client] = lambda: mock_client
    client = TestClient(app)

    # 1. Normal wa-conversation- should bypass get_chat_mode call
    payload = {
        "lady_id": "lady-lila",
        "lady_name": "Lila",
        "agency_id": "agency-diana1",
        "chat_id": "wa-conversation-12345",
        "sender_phone": "+49170111222",
        "message": "Hi, are you there?",
        "availability_context": {
            "timeZone": "Europe/Berlin",
            "slots": [
                {
                    "startsAt": "2026-10-16T18:00:00.000Z",
                    "endsAt": "2026-10-16T22:00:00.000Z",
                    "status": "available",
                }
            ],
        },
    }

    resp = client.post("/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ready", "sent")

    # 2. Operator takeover in payload should be honored
    takeover_payload = {
        **payload,
        "ownership_kind": "operator",
    }
    resp2 = client.post("/whatsapp/webhook", json=takeover_payload)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"
    assert resp2.json()["reason"] == "operator_takeover"

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_get_or_create_agent_cached_language_switch():
    from app.api.whatsapp import _AGENTS, get_or_create_agent
    from app.config import get_settings
    from app.sessions import MemorySessionStore

    settings = get_settings()
    store = MemorySessionStore()
    mock_client = MockArgenturaClient()

    lady_id = "test-switch-lady-direct"
    # Create with German
    agent1 = await get_or_create_agent(
        lady_id=lady_id,
        client=mock_client,
        settings=settings,
        store=store,
        response_language="de",
    )
    assert agent1.response_language == "de"
    assert lady_id in _AGENTS

    # Switch to English on cached agent without lady_context (exercises line 106)
    agent2 = await get_or_create_agent(
        lady_id=lady_id,
        client=mock_client,
        settings=settings,
        store=store,
        response_language="en",
    )
    assert agent2 is agent1
    assert agent2.response_language == "en"
    assert "English" in agent2.system_prompt or "en" in agent2.system_prompt


def test_lady_profile_outcall_and_address_from_lady_context():
    from app.argentura.models import LadyProfile

    lady_ctx = {
        "displayName": "Diana",
        "allowedVisitTypes": ["incall", "outcall"],
        "travelFeeRule": {
            "maxDistanceKm": 40,
            "amountMinor": 3500,
            "currencyCode": "EUR",
        },
        "incallAddress": {
            "street": "Berliner Str.",
            "houseNumber": "16",
            "zip": "10115",
            "city": "Berlin",
        },
        "services": [
            {"durationMinutes": 60, "amountMinor": 6000}
        ],
    }

    lady = LadyProfile.from_lady_context(lady_id="lady-diana", lady_context=lady_ctx)
    assert lady.name == "Diana"
    assert lady.incall is True
    assert lady.outcall is True
    assert lady.travelFee is not None
    assert lady.travelFee.maxDistanceKm == 40
    assert lady.travelFee.fee_eur == 35.0
    assert "Studio" not in lady.studio_address
    assert "Berliner Str. 16, 10115, Berlin" == lady.studio_address


@pytest.mark.anyio
async def test_webhook_with_booking_context_merging():
    from app.api.whatsapp import get_or_create_agent
    from app.config import get_settings
    from app.sessions import MemorySessionStore

    settings = get_settings()
    store = MemorySessionStore()
    mock_client = MockArgenturaClient()

    lady_ctx = {
        "displayName": "Diana",
        "services": [{"durationMinutes": 60, "amountMinor": 6000}],
    }
    booking_ctx = {
        "allowedVisitTypes": ["incall", "outcall"],
        "travelPlanning": {
            "feeRule": {
                "maxDistanceKm": 30,
                "amountMinor": 2500,
            }
        }
    }

    # Simulate webhook handler merging
    merged_ctx = dict(lady_ctx)
    if "allowedVisitTypes" in booking_ctx:
        merged_ctx["allowedVisitTypes"] = booking_ctx["allowedVisitTypes"]
    if "travelPlanning" in booking_ctx and "feeRule" in booking_ctx["travelPlanning"]:
        merged_ctx["travelFeeRule"] = booking_ctx["travelPlanning"]["feeRule"]

    agent = await get_or_create_agent(
        lady_id="test-merge-lady",
        client=mock_client,
        settings=settings,
        store=store,
        lady_context=merged_ctx,
        lady_name="Diana",
    )
    assert agent.lady.name == "Diana"
    assert agent.lady.outcall is True
    assert agent.lady.travelFee is not None
    assert agent.lady.travelFee.maxDistanceKm == 30
    assert agent.lady.travelFee.fee_eur == 25.0



