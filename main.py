from fastapi import FastAPI, Request
import os, httpx

app = FastAPI()

# TIP: dopo i test mettila in ENV su Render (Settings → Environment)
RINGOVER_API_KEY = os.getenv("RINGOVER_API_KEY", "b637bc5556e16016596eef12e03b75b88b4fb3aa")

HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

async def safe_delete(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.delete(url, headers=HEADERS, timeout=10)
        # 200/204 = cancellato; 404 = già non esiste → per noi è OK
        return r.status_code in (200, 204, 404)
    except Exception:
        return False

@app.get("/")
async def health():
    return {"status": "ok"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()

    call_id       = data.get("call_id")
    recording_id  = data.get("recording_id")
    # alcuni payload usano questi campi
    recording_url = data.get("recording_url") or data.get("audio_url")

    deleted = False
    async with httpx.AsyncClient() as client:
        # 1) se abbiamo recording_id: endpoint diretto
        if recording_id and not deleted:
            deleted = await safe_delete(client, f"https://public-api.ringover.com/recordings/{recording_id}")

        # 2) fallback via call_id (alcune org lo usano)
        if call_id and not deleted:
            # vecchio endpoint
            deleted = await safe_delete(client, f"https://public-api.ringover.com/recordings/{call_id}")
        if call_id and not deleted:
            # endpoint v2 (alcune aziende lo espongono)
            deleted = await safe_delete(client, f"https://public-api.ringover.com/v2/calls/{call_id}/recordings")

        # 3) se abbiamo solo l’URL NON lo salviamo, NON lo logghiamo (privacy). Ignoriamo.

    return {"status": "ok", "deleted": bool(deleted)}

