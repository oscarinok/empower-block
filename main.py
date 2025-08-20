from fastapi import FastAPI, Request, BackgroundTasks
import re, json, asyncio, httpx

app = FastAPI()

RINGOVER_API_KEY = "4dda64fbf2ac86463b995be126b906fec64b1cb2"
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}
INITIAL_DELAY = 90
RETRY_DELAYS = [180, 300]
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
API_BASE = "https://public-api.ringover.com"

async def delete_by_recording_id(client, rec_id: str) -> bool:
    url = f"{API_BASE}/recordings/{rec_id}"
    r = await client.delete(url, headers=HEADERS, timeout=12)
    print(f"[DELETE rec] {rec_id} -> {r.status_code}")
    return r.status_code in (200, 204, 404)

async def delete_all_by_call_id(client, call_id: str) -> bool:
    url = f"{API_BASE}/v2/calls/{call_id}/recordings"
    r = await client.delete(url, headers=HEADERS, timeout=12)
    print(f"[DELETE call(all rec)] {call_id} -> {r.status_code}")
    return r.status_code in (200, 204, 404)

async def list_recordings_for_call(client, call_id: str) -> list[str]:
    url = f"{API_BASE}/v2/calls/{call_id}"
    r = await client.get(url, headers=HEADERS, timeout=12)
    print(f"[GET call] {call_id} -> {r.status_code}")
    if r.status_code != 200 or not r.content:
        return []
    data = r.json()
    recs = []
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
    print(f"[LIST rec] {recs}")
    return list({*recs})

def find_all_uuids(obj) -> set[str]:
    """Cerca UUID ovunque nel payload (stringhe e stringify del JSON)."""
    uuids = set()
    try:
        if isinstance(obj, dict):
            for v in obj.values():
                uuids |= find_all_uuids(v)
        elif isinstance(obj, list):
            for v in obj:
                uuids |= find_all_uuids(v)
        elif isinstance(obj, str):
            for m in UUID_RE.finditer(obj):
                uuids.add(m.group(0))
    except Exception:
        pass
    try:
        txt = json.dumps(obj, ensure_ascii=False)
        for m in UUID_RE.finditer(txt):
            uuids.add(m.group(0))
    except Exception:
        pass
    return uuids

async def attempt_cleanup(call_id, recording_id, audio_url, payload):
    async with httpx.AsyncClient() as client:
        # 1) recording_id esplicito
        if recording_id and await delete_by_recording_id(client, recording_id):
            return True
        # 2) da audio_url
        if audio_url:
            m = UUID_RE.search(audio_url)
            if m and await delete_by_recording_id(client, m.group(0)):
                return True
        # 3) via call_id (delete all)
        if call_id and await delete_all_by_call_id(client, call_id):
            return True
        # 4) fallback: prova tutti gli UUID trovati nel payload come recording_id
        ids = find_all_uuids(payload)
        print(f"[SCAN UUIDs] found={ids}")
        for rid in ids:
            if await delete_by_recording_id(client, rid):
                return True
        # 5) ultima spiaggia: se abbiamo call_id prova list & delete
        if call_id:
            for rid in await list_recordings_for_call(client, call_id):
                await delete_by_recording_id(client, rid)
            return True
        return False

async def scheduled_cleanup(call_id, recording_id, audio_url, payload):
    await asyncio.sleep(INITIAL_DELAY)
    if await attempt_cleanup(call_id, recording_id, audio_url, payload):
        print("[CLEAN] done at t≈90s")
        return
    for abs_delay in RETRY_DELAYS:
        await asyncio.sleep(abs_delay - INITIAL_DELAY)
        if await attempt_cleanup(call_id, recording_id, audio_url, payload):
            print(f"[CLEAN] done at t≈{abs_delay}s")
            return
    print("[CLEAN] gave up")

@app.get("/")
async def health():
    return {"status": "running", "mode": "debug-block", "initial_delay_sec": INITIAL_DELAY}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        data = {}

    # prova con nomi “classici”
    call_id      = data.get("call_id") or data.get("call_uuid")
    recording_id = data.get("recording_id")
    audio_url    = data.get("recording_url") or data.get("audio_url") or data.get("audioFileLink")

    # DEBUG: logga struttura payload
    try:
        top_keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        print(f"[WEBHOOK] keys={top_keys}")
        # stampa alcuni campi tipici se esistono
        cd = data.get("call_details") if isinstance(data, dict) else None
        if isinstance(cd, dict):
            print(f"[WEBHOOK] call_details keys={list(cd.keys())}")
    except Exception:
        pass

    print(f"[WEBHOOK] call_id={call_id} recording_id={recording_id} audio_url={audio_url}")

    background_tasks.add_task(scheduled_cleanup, call_id, recording_id, audio_url, data)
    return {"status": "ok", "queued": True}
