# main.py
from fastapi import FastAPI, Request, BackgroundTasks
import os, asyncio, httpx

app = FastAPI()

# 🔐 Metti la chiave su Render > Settings > Environment: RINGOVER_API_KEY=...
RINGOVER_API_KEY = os.getenv("RINGOVER_API_KEY", "b637bc5556e16016596eef12e03b75b88b4fb3aa")
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

async def delayed_delete(call_id: str | None, recording_id: str | None, delay_sec: int = 180):
    # aspetta che Empower finisca di processare (trascrizione, score, summary)
    await asyncio.sleep(delay_sec)
    async with httpx.AsyncClient(timeout=8) as client:
        # 1) endpoint diretto se abbiamo recording_id
        if recording_id:
            try:
                r = await client.delete(
                    f"https://public-api.ringover.com/recordings/{recording_id}",
                    headers=HEADERS
                )
                if r.status_code in (200, 204, 404):  # 404 = già sparito → ok
                    return
            except Exception:
                pass
        # 2) fallback via call_id (API v2)
        if call_id:
            try:
                await client.delete(
                    f"https://public-api.ringover.com/v2/calls/{call_id}/recordings",
                    headers=HEADERS
                )
            except Exception:
                pass

@app.get("/")
async def health():
    return {"status": "running", "mode": "delayed_delete", "delay_sec": 180}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    # non bloccare Empower: leggi payload e ritorna SUBITO 200
    try:
        data = await request.json()
    except Exception:
        data = {}

    call_id      = data.get("call_id") or data.get("call_uuid")
    recording_id = data.get("recording_id")

    background_tasks.add_task(delayed_delete, call_id, recording_id, 180)  # 3 minuti
    return {"status": "ok", "queued": True}
