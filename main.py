from fastapi import FastAPI, Request, BackgroundTasks
import re, asyncio, httpx

app = FastAPI()

# 🔴 CHIAVE RINGOVER HARDCODED (come richiesto)
RINGOVER_API_KEY = "4dda64fbf2ac86463b995be126b906fec64b1cb2"
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

# ritardo breve + retry (per lasciare finire trascrizione)
INITIAL_DELAY = 90           # 1,5 min
RETRY_DELAYS = [180, 300]    # retry a 3 e 5 min
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

async def delete_by_recording_id(client: httpx.AsyncClient, rec_id: str) -> bool:
    r = await client.delete(f"https://public-api.ringover.com/recordings/{rec_id}",
                            headers=HEADERS, timeout=12)
    return r.status_code in (200, 204, 404)

async def delete_by_call_id(client: httpx.AsyncClient, call_id: str) -> bool:
    r = await client.delete(f"https://public-api.ringover.com/v2/calls/{call_id}/recordings",
                            headers=HEADERS, timeout=12)
    return r.status_code in (200, 204, 404)

async def list_recordings_for_call(client: httpx.AsyncClient, call_id: str) -> list[str]:
    r = await client.get(f"https://public-api.ringover.com/v2/calls/{call_id}",
                         headers=HEADERS, timeout=12)
    if r.status_code != 200 or not r.content:
        return []
    data = r.json()
    recs: list[str] = []
    for k in ("recordings", "recording_ids", "audio", "media"):
        v = data.get(k)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    recs.append(it)
                elif isinstance(it, dict):
                    rid = it.get("id") or it.get("recording_id")
                    if rid:
                        recs.append(rid)
    return list({x for x in recs if isinstance(x, str) and x})

async def attempt_cleanup(call_id: str | None, recording_id: str | None, audio_url: str | None) -> bool:
    async with httpx.AsyncClient() as client:
        # 1) se abbiamo recording_id
        if recording_id and await delete_by_recording_id(client, recording_id):
            return True
        # 2) prova a estrarre UUID dal link audio
        if audio_url:
            m = UUID_RE.search(audio_url)
            if m and await delete_by_recording_id(client, m.group(0)):
                return True
        # 3) cancella tutto per call_id
        if call_id and await delete_by_call_id(client, call_id):
            return True
        # 4) ultima spiaggia: lista recording e cancellali uno a uno
        if call_id:
            for rid in await list_recordings_for_call(client, call_id):
                await delete_by_recording_id(client, rid)
            return True
        return False

async def scheduled_cleanup(call_id, recording_id, audio_url):
    await asyncio.sleep(INITIAL_DELAY)  # primo tentativo
    if await attempt_cleanup(call_id, recording_id, audio_url):
        return
    # retry a 3 e 5 minuti (se l’elaborazione chiude tardi)
    for abs_delay in RETRY_DELAYS:
        await asyncio.sleep(abs_delay - INITIAL_DELAY)
        if await attempt_cleanup(call_id, recording_id, audio_url):
            return

@app.get("/")
async def health():
    return {"status": "running", "mode": "block-download", "initial_delay_sec": INITIAL_DELAY}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        data = {}

    call_id      = data.get("call_id") or data.get("call_uuid")
    recording_id = data.get("recording_id")
    audio_url    = data.get("recording_url") or data.get("audio_url")

    background_tasks.add_task(scheduled_cleanup, call_id, recording_id, audio_url)
    return {"status": "ok", "queued": True}
