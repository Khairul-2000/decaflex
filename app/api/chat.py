import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.guards import DAILY_CAP_REPLY, RATE_LIMIT_REPLY

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=2000)
    age_confirmed: bool = False


def _sse(payload: dict | str) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {body}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    agent = getattr(request.app.state, "argentura_agent", None)
    luna = getattr(request.app.state, "luna", None)
    limiter = request.app.state.limiter

    # 18+ gate: the client must confirm once per session.
    if req.age_confirmed:
        if luna:
            await luna.confirm_adult(req.session_id)
        elif agent:
            data = await agent.store.get(req.session_id)
            if not data.minor:
                data.adult = True
                await agent.store.save(req.session_id, data)

    # Check adult
    if luna and not await luna.is_adult(req.session_id):
        data = await luna.store.get(req.session_id)
        if not data.minor:
            raise HTTPException(status_code=403, detail="age_confirmation_required")
    elif agent:
        data = await agent.store.get(req.session_id)
        if not data.adult and not data.minor:
            raise HTTPException(status_code=403, detail="age_confirmation_required")

    blocked = limiter.check(req.session_id)
    if blocked:
        detail = DAILY_CAP_REPLY if blocked == "daily" else RATE_LIMIT_REPLY
        raise HTTPException(
            status_code=429, detail=detail, headers={"Retry-After": "30"}
        )

    async def event_stream():
        if agent:
            reply = await agent.reply(req.session_id, req.message)
            yield _sse({"delta": reply or ""})
        elif luna:
            async for piece in luna.stream(req.session_id, req.message):
                yield _sse({"delta": piece})
        yield _sse("[DONE]")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/chat/{session_id}")
async def reset_chat(session_id: str, request: Request):
    agent = getattr(request.app.state, "argentura_agent", None)
    luna = getattr(request.app.state, "luna", None)
    if agent:
        await agent.reset(session_id)
    elif luna:
        await luna.reset(session_id)
    return {"status": "cleared"}
