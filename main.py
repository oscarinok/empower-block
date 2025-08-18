from fastapi import FastAPI, Request
from fastapi.background import BackgroundTasks
import os, httpx

app = FastAPI()

# 👉 dopo i test, mettila come ENV su Render (Settings → Environment)
RINGOVER_API_KEY = os.getenv("RINGOVER_API_KEY", "b637bc5556e16016596eef12e03b75b88b4fb3aa")
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

async def delete_recording(call_id: str | None, recording_id: str | None):
    async with httpx.AsyncClient(timeout=6) as client:
        # 1) prova con recording_id (endpoint diretto)
        if recording_id:
            try:
                r = await client.delete(f"https://public-api.ringover.com/recordings/{recording_id}", headers=HEADERS)
                if r.status_code in (200, 204, 404):
                    return
            except Exception:
                pass
        # 2) fallback con call_id (v2)
        if call_id:
            try:
                r = await client.delete(f"https://public-api.ringover.com/v2/calls/{call_id}/recordings", headers=HEADERS)
            except Exception:
                pass

@app.get("/")
async def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    # 1) leggi payload senza fallire
    try:
        data = await request.json()
    except Exception:
        data = {}

    call_id      = data.get("call_id") or data.get("call_uuid")
    recording_id = data.get("recording_id")

    # 2) rispondi SUBITO 200 → Empower continua a funzionare
    background_tasks.add_task(delete_recording, call_id, recording_id)
    return {"status": "ok"}   # nessun errore, nessuna attesa

