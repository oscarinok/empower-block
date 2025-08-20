from fastapi import FastAPI, Request, BackgroundTasks
import re, asyncio, httpx

app = FastAPI()

# 🔴 La tua API key Ringover (come richiesto, hardcoded)
RINGOVER_API_KEY = "4dda64fbf2ac86463b995be126b906fec64b1cb2"
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

# ritardo iniziale: 90s. Se serve, alza a 120.
INITIAL_DELAY = 90
# retry “di sicurezza” nel caso Empower chiuda più tardi
RETRY_DELAYS = [180, 300]

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

API_BASE = "https://public-api.ringover.com"

async def delete_by_recording_id(client: httpx.AsyncClient, rec_id: str) -> bool:
    url = f"{API_BASE}/recordings/{rec_id}"
    r = await client.delete(url, headers=HEADERS, timeout=12)
    print(f"[DELETE rec] {url} -> {r.status_code}")
    return r.status_code in (200, 204, 404)

async def delete_all_by_call_id(client: httpx.AsyncClient, call_id: str) -> bool:
    url = f"{API_BASE}/v2/calls/{call_id}/recordings"
    r = await client.delete(url, headers=HEADERS, timeout=12)
    print(f"[DELETE call] {url} -> {r.status_code}")
    return r.status_code in (200, 204, 404)

async def list_recordings_for_call(client: httpx.AsyncClient, call_id: str) -> list[str]:
    url = f"{API_BASE}/v2/calls/{call_id}"
    r = await client.get(url, headers=HEADERS, timeout=12)
    print(f"[GET call] {url} -> {r.status_code}")
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
    recs = [x for x in recs if isinstance(x, str) and x]
    print(f"[LIST rec] found={recs}")
    return list({*recs})

async def attempt_cleanup(call_id: str | None, recording_id: str | None, audio_url: str | None) -> bool:
    async with httpx.AsyncClient() as client:
        # 1) recording_id esplicito
        if recording_id:
            print(f"[CLEAN] try recording_id={recording_id}")
            if await delete_by_recording_id(client, recording_id):
                return True
        # 2) prova a estrarre un UUID dal link audio
        if audio_url:
            m = UUID_RE.search(audio_url)
            print(f"[CLEAN] audio_url={audio_url} uuid={m.group(0) if m else None}")
            if m and await delete_by_recording_id(client, m.group(0)):
                return True
        # 3) via call_id (delete all)
        if call_id:
            print(f"[CLEAN] try delete all for call_id={call_id}")
            if await delete_all_by_call_id(client, call_id):
                return True
            # 4) fallback: lista recording e cancellali uno-a-uno
            for rid in await list_recordings_for_call(client, call_id):
                await delete_by_recording_id(client, rid)
            return True
        return False

async def scheduled_cleanup(call_id, recording_id, audio_url):
    # primo tentativo
    await asyncio.sleep(INITIAL_DELAY)
    ok = await attempt_cleanup(call_id, recording_id, audio_url)
    if ok:
        print("[CLEAN] done at t≈90s")
        return
    # retry a 3 e 5 minuti
    for abs_delay in RETRY_DELAYS:
        await asyncio.sleep(abs_delay - INITIAL_DELAY)
        if await attempt_cleanup(call_id, recording_id, audio_url):
            print(f"[CLEAN] done at t≈{abs_delay}s")
            return
    print("[CLEAN] gave up")

@app.get("/")
async def health():
    return {"status": "running", "mode": "block-download", "initial_delay_sec": INITIAL_DELAY}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        data = {}
    # prendi i campi con più nomi possibili
    call_id      = data.get("call_id") or data.get("call_uuid")
    recording_id = data.get("recording_id")
    audio_url    = data.get("recording_url") or data.get("audio_url") or data.get("audioFileLink")

    print(f"[WEBHOOK] call_id={call_id} recording_id={recording_id} audio_url={audio_url}")
    background_tasks.add_task(scheduled_cleanup, call_id, recording_id, audio_url)
    return {"status": "ok", "queued": True}
