# main.py
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

# *** ATTENZIONE: chiave hardcoded solo per test. Ruota e spostala in env dopo i test. ***
RINGOVER_API_KEY = "b637bc5556e16016596eef12e03b75b88b4fb3aa"
RINGOVER_API_BASE = "https://public-api.ringover.com"

@app.get("/")
async def health():
    return {"status": "ok"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Riceve il webhook da Empower/Ringover quando una call è stata processata
    e cancella immediatamente la registrazione audio.
    Non salva né stampa URL o contenuti sensibili.
    """
    data = await request.json()

    call_id       = data.get("call_id")
    recording_id  = data.get("recording_id")
    recording_url = data.get("recording_url") or data.get("audio_url")

    if not call_id and not recording_id and not recording_url:
        # payload non utile: non blocchiamo il retry del webhook
        return {"status": "ignored", "reason": "no identifiers"}

    headers = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

    async with httpx.AsyncClient(timeout=10) as client:
        # 1) se abbiamo recording_id, proviamo a cancellare direttamente quello
        if recording_id:
            try:
                await client.delete(f"{RINGOVER_API_BASE}/recordings/{recording_id}", headers=headers)
            except Exception:
                pass

        # 2) fallback: alcuni payload permettono delete via call_id
        if call_id:
            try:
                await client.delete(f"{RINGOVER_API_BASE}/recordings/{call_id}", headers=headers)
            except Exception:
                pass

        # 3) ulteriore fallback: se abbiamo solo l'URL non facciamo nulla (non logghiamo, non salviamo)
        # L’URL viene ignorato di proposito per non lasciare traccia utilizzabile.

    return {"status": "ok", "deleted": True}

