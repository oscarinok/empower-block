from fastapi import FastAPI, Request, BackgroundTasks
import asyncio, httpx, re

app = FastAPI()

# --- CONFIG ---
RINGOVER_API_KEY = "4dda64fbf2ac86463b995be126b906fec64b1cb2"  # la tua
API_BASE = "https://public-api.ringover.com"
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}
INITIAL_DELAY = 90                 # primo tentativo dopo 90s
RETRY_AT = [180, 300]              # retry a 3 e 5 minuti
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# ---- helper ----
async def _delete_rec_by_id(client: httpx.AsyncClient, rec_id: str) -> bool:
    url = f"{API_BASE}/recordings/{rec_id}"
    r = await client.delete(url, headers=HEADERS, timeout=12)
    print(f"[DELETE rec] {rec_id} -> {r.status_code} {r.text[:200]}")
    return r.status_code in (200, 204, 404)

async def _delete_all_by_call_id(client: httpx.AsyncClient, call_id: str) -> bool:
    # v2 call-id bulk
    url = f"{API_BASE}/v2/calls/{call_id}/recordings"
    r = await client.delete(url, headers=HEADERS, timeout=12)
    print(f"[DELETE call/all] {call_id} -> {r.status_code} {r.text[:200]}")
    return r.status_code in (200, 204, 404)

async def _get_call_by_id(client: httpx.AsyncClient, call_id: str):
    url = f"{API_BASE}/v2/calls/{call_id}"
    r = await client.get(url, headers=HEADERS, timeout=12)
    print(f"[GET call id] {call_id} -> {r.status_code}")
    return r.json() if r.status_code == 200 else None

async def _get_call_by_uuid(client: httpx.AsyncClient, call_uuid: str):
    # alcuni tenant espongono endpoint per uuid
    for path in (f"/v2/calls/uuid/{call_uuid}", f"/calls/{call_uuid}"):
        url = f"{API_BASE}{path}"
        r = await client.get(url, headers=HEADERS, timeout=12)
        print(f"[GET call uuid] {path} -> {r.status_code}")
        if r.status_code == 200:
            return r.json()
    return None

def _extract_rec_ids(call_json) -> list[str]:
    if not isinstance(call_json, dict):
        return []
    recs = set()
    for k in ("recordings", "recording_ids", "media", "audio"):
        v = call_json.get(k)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    recs.add(it)
                elif isinstance(it, dict):
                    rid = it.get("id") or it.get("recording_id")
                    if rid:
                        recs.add(rid)
    return list(recs)

async def _try_all_paths(call_id: str|None, call_uuid: str|None, recording_id: str|None, audio_url: str|None) -> bool:
    async with httpx.AsyncClient() as client:
        # 1) abbiamo già recording_id?
        if recording_id and await _delete_rec_by_id(client, recording_id):
            return True

        # 2) proviamo a estrarre un UUID dal link audio
        if audio_url:
            m = UUID_RE.search(audio_url)
            if m and await _delete_rec_by_id(client, m.group(0)):
                return True

        # 3) se c'è call_id, prova bulk delete
        if call_id and await _delete_all_by_call_id(client, call_id):
            return True

        # 4) prova a recuperare la call via call_id e cancellare ogni recording
        if call_id:
            cj = await _get_call_by_id(client, call_id)
            for rid in _extract_rec_ids(cj):
                await _delete_rec_by_id(client, rid)
            if cj and _extract_rec_ids(cj):
                return True

        # 5) prova via call_uuid (alcuni tenant danno i dati solo così)
        if call_uuid:
            cj = await _get_call_by_uuid(client, call_uuid)
            for rid in _extract_rec_ids(cj):
                await _delete_rec_by_id(client, rid)
            if cj and _extract_rec_ids(cj):
                return True

        # 6) ultimo tentativo: se abbiamo audio_url ma senza uuid, prova delete su call (se presente)
        if call_id:
            return await _delete_all_by_call_id(client, call_id)

        return False

async def _schedule_cleanup(call_id, call_uuid, recording_id, audio_url):
    # primo tentativo
    await asyncio.sleep(INITIAL_DELAY)
    ok = await _try_all_paths(call_id, call_uuid, recording_id, audio_url)
    if ok:
        print("[CLEAN] done @~90s"); return
    # retry extra
    for t in RETRY_AT:
        await asyncio.sleep(t - INITIAL_DELAY)
        if await _try_all_paths(call_id, call_uuid, recording_id, audio_url):
            print(f"[CLEAN] done @{t}s"); return
    print("[CLEAN] gave up")

# --- FastAPI ---
@app.get("/")
async def health():
    return {"status":"running","mode":"fetch-and-delete","delay":INITIAL_DELAY}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        data = {}

    # prova a leggere dai diversi formati (Empower / Ringover)
    call_id       = data.get("call_id") or (data.get("call_details") or {}).get("ringover_id")
    call_uuid     = data.get("call_uuid") or (data.get("call_details") or {}).get("call_uuid")
    recording_id  = data.get("recording_id")
    audio_url     = data.get("audio_url") or data.get("recording_url")

    print(f"[WEBHOOK] call_id={call_id} call_uuid={call_uuid} recording_id={recording_id} audio_url={audio_url}")

    background_tasks.add_task(_schedule_cleanup, call_id, call_uuid, recording_id, audio_url)
    return {"status":"ok","queued":True}
