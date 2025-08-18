from fastapi import FastAPI, Request
import asyncio
import httpx
import os

app = FastAPI()

# URL API Pipedrive (qui va messo quello giusto della tua azienda)
PIPEDRIVE_URL = "https://api.pipedrive.com/v1/notes"
PIPEDRIVE_TOKEN = os.getenv("PIPEDRIVE_TOKEN")  # tienilo sicuro come variabile

@app.get("/")
async def health():
    return {"status": "running", "mode": "clean_audio_links"}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    # Attendere 3 minuti prima di processare
    await asyncio.sleep(180)

    # Rimuovere audioFileLink se presente
    if "call_details" in data and "audioFileLink" in data["call_details"]:
        del data["call_details"]["audioFileLink"]

    # Esempio: mandare il testo a Pipedrive come nota
    note_content = f"Call UUID: {data['call_details'].get('call_uuid', '')}\n\n"
    if "call_score" in data:
        note_content += f"Score: {data['call_score']}\n\n"
    if "transcription" in data:
        note_content += f"Transcript: {data['transcription']}\n"

    async with httpx.AsyncClient() as client:
        await client.post(
            PIPEDRIVE_URL,
            params={"api_token": PIPEDRIVE_TOKEN},
            json={"content": note_content}
        )

    return {"status": "ok", "action": "cleaned", "note_sent": True}
