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
