import json

from fastapi.testclient import TestClient

from app.main import create_app

SID = "web-session-0001"


def parse_sse(text: str) -> str:
    out = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            payload = block[5:].strip()
            if payload != "[DONE]":
                out.append(json.loads(payload)["delta"])
    return "".join(out).strip()


def client_for(settings, luna):
    return TestClient(create_app(settings, luna=luna, start_telegram=False))


def test_health(settings, make_luna):
    luna, _ = make_luna()
    with client_for(settings, luna) as c:
        r = c.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"


def test_index_served(settings, make_luna):
    luna, _ = make_luna()
    with client_for(settings, luna) as c:
        r = c.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "online"
        assert "endpoints" in data


def test_age_gate_blocks_until_confirmed(settings, make_luna):
    luna, llm = make_luna()
    with client_for(settings, luna) as c:
        r = c.post("/chat", json={"session_id": SID, "message": "hey"})
        assert r.status_code == 403
        assert llm.calls == []


def test_chat_streams_and_keeps_history(settings, make_luna):
    luna, llm = make_luna(["Hey you 😏 how's your day", "Tell me more about the coffee"])
    with client_for(settings, luna) as c:
        r1 = c.post("/chat", json={"session_id": SID, "message": "hey luna", "age_confirmed": True})
        assert r1.status_code == 200
        assert parse_sse(r1.text) == "Hey you 😏 how's your day"

        r2 = c.post("/chat", json={"session_id": SID, "message": "i had coffee"})
        assert parse_sse(r2.text) == "Tell me more about the coffee"

    # second prompt = system + (user, assistant) from turn 1 + new user message
    second = llm.calls[1]
    assert len(second) == 4
    assert "Luna" in second[0].content
    assert second[1].content == "hey luna"
    assert second[3].content == "i had coffee"


def test_minor_is_stopped_and_stays_stopped(settings, make_luna):
    luna, llm = make_luna()
    with client_for(settings, luna) as c:
        r1 = c.post("/chat", json={"session_id": SID, "message": "im 15 years old", "age_confirmed": True})
        assert "adults" in parse_sse(r1.text)
        r2 = c.post("/chat", json={"session_id": SID, "message": "hey", "age_confirmed": True})
        assert "under 18" in parse_sse(r2.text)
        assert llm.calls == []  # the model was never called


def test_rate_limit(settings, make_luna):
    luna, _ = make_luna()
    with client_for(settings, luna) as c:
        body = {"session_id": SID, "message": "hi", "age_confirmed": True}
        codes = [c.post("/chat", json=body).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]


def test_crisis_note_is_added_to_prompt(settings, make_luna):
    luna, llm = make_luna(["I'm here with you."])
    with client_for(settings, luna) as c:
        c.post("/chat", json={"session_id": SID, "message": "i want to die", "age_confirmed": True})
    assert "self-harm" in llm.calls[0][0].content


def test_reset_clears_history(settings, make_luna):
    luna, llm = make_luna(["one", "two"])
    with client_for(settings, luna) as c:
        c.post("/chat", json={"session_id": SID, "message": "first", "age_confirmed": True})
        assert c.delete(f"/chat/{SID}").json() == {"status": "cleared"}
        c.post("/chat", json={"session_id": SID, "message": "second"})
    assert len(llm.calls[1]) == 2  # system + new message only


def test_bad_session_id_rejected(settings, make_luna):
    luna, _ = make_luna()
    with client_for(settings, luna) as c:
        r = c.post("/chat", json={"session_id": "bad id!!", "message": "hi", "age_confirmed": True})
        assert r.status_code == 422
