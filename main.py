# main.py
from fastapi import FastAPI, Request, BackgroundTasks
import os, asyncio, httpx

app = FastAPI()

# 🔐 Meglio metterla in Render > Settings > Environment (RINGOVER_API_KEY)
RINGOVER_API_KEY = os.getenv("RINGOVER_API_KEY", "b637bc5556e16016596eef12e03b75b88b4fb3aa")
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

# quanto attendere prima di cancellare l'audio (in sec)
DELAY_SEC = int(os.getenv("DELETE_DELAY_SEC", "180"))  # 3 minuti

async def delayed_delete(call_id: str | None, recording_id: str | None):
    # 1) aspetta che Empower finisca di processare
    await asyncio.sleep(DELAY_SEC)

    async with httpx.AsyncClient(timeout=8) as client:
        # 2) prova a cancellare tramite recording_id (endpoint diretto)
        if recording_id:
            try:
                r = await client.delete(
                    f"https://public-api.ringover.com/recordings/{recording_id}",
                    headers=HEADERS,
                )
                # 200/204 = cancellato, 404 = già sparito → ok
                if r.status_code in (200, 204, 404):
                    return
            except Exception:
                pass

        # 3) fallback via call_id (API v2)
        if call_id:
            try:
                await client.delete(
                    f"https://public-api.ringover.com/v2/calls/{call_id}/recordings",
                    headers=HEADERS,
                )
            except Exception:
                pass

@app.get("/")
async def health():
    return {"status": "running", "mode": "delayed_delete", "delay_sec": DELAY_SEC}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    # ricevi il webhook da Empower e rispondi SUBITO 200 (non blocca nulla)
    try:
        data = await request.json()
    except Exception:
        data = {}

    # gli ID possono arrivare con nomi diversi
    call_id      = data.get("call_id") or data.get("call_uuid")
    recording_id = data.get("recording_id")

    # programma la cancellazione in background
    background_tasks.add_task(delayed_delete, call_id, recording_id)

    return {"status": "ok", "queued": True}
