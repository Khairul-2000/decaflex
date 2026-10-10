from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    bot = getattr(request.app.state, "telegram", None)
    if bot is None:
        raise HTTPException(status_code=404, detail="telegram_disabled")

    secret = request.app.state.settings.telegram_webhook_secret
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        raise HTTPException(status_code=403, detail="bad_secret")

    update = await request.json()
    bot.spawn(update)  # answer Telegram immediately, process in the background
    return {"ok": True}
