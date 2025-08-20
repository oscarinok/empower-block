from fastapi import FastAPI, Request, BackgroundTasks
import asyncio, httpx, re, time
from typing import Optional, Dict, Tuple, List

app = FastAPI()

# ========= CONFIG =========
# TUA API KEY (come da screenshot)
RINGOVER_API_KEY = "4dda64fbf2ac86463b995be126b906fec64b1cb2"

API_BASE = "https://public-api.ringover.com"
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

# primo tentativo dopo 90s, poi retry a 180s e 300s
INITIAL_DELAY = 90
RETRY_AT = [180, 300]

# controllo semplice di UUID in eventuali URL
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# accodiamo le call senza recording_id:
# pending[(call_id, call_uuid)] = {"added": epoch, "tries": n}
pending: Dict[Tuple[Optional[str], Optional[str]], Dict[str, float]] = {}
PENDING_CHECK_PERIOD = 60  # ogni 60s ricontrollo

# ========= HELPER HTTP =========
async def _delete_rec_by_id(client: httpx.AsyncClient, rec_id: str) -> bool:
    url = f"{API_BASE}/recordings/{rec_id}"
    r = await client.delete(url, headers=HEADERS, timeout=15)
    print(f"[DELETE rec] {rec_id} -> {r.status_code} {r.text[:160]}")
    return r.status_code in (200, 204, 404)

async def _delete_all_by_call_id(client: httpx.AsyncClient, call_id: str) -> bool:
    # alcuni tenant espongono il bulk su /v2/calls/{id}/recordings
    url = f"{API_BASE}/v2/calls/{call_id}/recordings"
    r = await client.delete(url, headers=HEADERS, timeout=15)
    print(f"[DELETE call/all] {call_id} -> {r.status_code} {r.text[:160]}")
    return r.status_code in (200, 204, 404)

async def _get_call_by_id(client: httpx.AsyncClient, call_id: str):
    url = f"{API_BASE}/v2/calls/{call_id}"
    r = await client.get(url, headers=HEADERS, timeout=15)
    print(f"[GET call id] {call_id} -> {r.status_code}")
    return r.json() if r.status_code == 200 else None

async def _get_call_by_uuid(client: httpx.AsyncClient, call_uuid: str):
    # tentiamo due path diversi
    for path in (f"/v2/calls/uuid/{call_uuid}", f"/calls/{call_uuid}"):
        url = f"{API_BASE}{path}"
        r = await client.get(url, headers=HEADERS, timeout=15)
        print(f"[GET call uuid] {path} -> {r.status_code}")
        if r.status_code == 200:
            return r.json()
    return None

def _extract_rec_ids(call_json) -> List[str]:
    if not isinstance(call_json, dict):
        return []
    recs = set()
    # proviamo vari campi comuni
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
    # alcuni payload mettono direttamente "recording_id"
    if call_json.get("recording_id"):
        recs.add(str(call_json["recording_id"]))
    return list(recs)

async def _try_delete_paths(call_id: Optional[str], call_uuid: Optional[str],
                            recording_id: Optional[str], audio_url: Optional[str]) -> bool:
    async with httpx.AsyncClient() as client:
        # 1) recording_id diretto
        if recording_id:
            if await _delete_rec_by_id(client, recording_id):
                return True

        # 2) estrai uuid dall'audio_url
        if audio_url:
            m = UUID_RE.search(audio_url)
            if m and await _delete_rec_by_id(client, m.group(0)):
                return True

        # 3) bulk da call_id
        if call_id and await _delete_all_by_call_id(client, call_id):
            return True

        # 4) GET call by id → cancella tutti i recording trovati
        if call_id:
            cj = await _get_call_by_id(client, call_id)
            ids = _extract_rec_ids(cj)
            ok_any = False
            for rid in ids:
                ok = await _delete_rec_by_id(client, rid)
                ok_any = ok_any or ok
            if ok_any:
                return True

        # 5) GET call by uuid → come sopra
        if call_uuid:
            cj = await _get_call_by_uuid(client, call_uuid)
            ids = _extract_rec_ids(cj)
            ok_any = False
            for rid in ids:
                ok = await _delete_rec_by_id(client, rid)
                ok_any = ok_any or ok
            if ok_any:
                return True

        # 6) fallback finale: se abbiamo call_id, ritenta bulk
        if call_id:
            return await _delete_all_by_call_id(client, call_id)

        return False

# ========= SCHEDULING =========
async def _delayed_cleanup(call_id: Optional[str], call_uuid: Optional[str],
                           recording_id: Optional[str], audio_url: Optional[str]):
    # primo tentativo differito
    await asyncio.sleep(INITIAL_DELAY)
    ok = await _try_delete_paths(call_id, call_uuid, recording_id, audio_url)
    if ok:
        print("[CLEAN] done @~90s")
        return

    # se non è andata, mettiamo in pending per i retry periodici
    key = (call_id, call_uuid)
    pending[key] = {"added": time.time(), "tries": 0}
    print(f"[QUEUE] added pending {key}")

async def _process_pending_forever():
    while True:
        try:
            if pending:
                keys = list(pending.keys())
                for key in keys:
                    call_id, call_uuid = key
                    meta = pending.get(key, {"tries": 0})
                    tries = meta.get("tries", 0) + 1
                    pending[key]["tries"] = tries

                    print(f"[RETRY] {key} try#{tries}")
                    ok = await _try_delete_paths(call_id, call_uuid, None, None)
                    if ok:
                        print(f"[CLEAN] pending {key} removed")
                        pending.pop(key, None)
                        continue

                    # stoppo dopo ultimo retry (tempo ~5 minuti)
                    if tries >= 2:  # abbiamo già fatto il primo differito + 2 retry timer → fine
                        print(f"[GIVEUP] {key}")
                        pending.pop(key, None)
            await asyncio.sleep(PENDING_CHECK_PERIOD)
        except Exception as e:
            print("[PENDING LOOP ERROR]", e)
            await asyncio.sleep(PENDING_CHECK_PERIOD)

# ========= FASTAPI =========
@app.on_event("startup")
async def _startup():
    asyncio.create_task(_process_pending_forever())

@app.get("/")
async def health():
    return {
        "status": "running",
        "mode": "fetch-and-delete",
        "delay": INITIAL_DELAY,
        "pending": len(pending)
    }

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        data = {}

    # normalizziamo i campi possibili (Empower / Ringover)
    call_details = data.get("call_details") or {}
    call_id      = data.get("call_id") or call_details.get("ringover_id")
    call_uuid    = data.get("call_uuid") or call_details.get("call_uuid")
    recording_id = data.get("recording_id")
    audio_url    = data.get("audio_url") or data.get("recording_url")

    print(f"[WEBHOOK] call_id={call_id} call_uuid={call_uuid} recording_id={recording_id} audio_url={audio_url}")

    # schedulo il ciclo di pulizia (differito + eventuale coda)
    background_tasks.add_task(_delayed_cleanup, call_id, call_uuid, recording_id, audio_url)
    return {"status": "ok", "queued": True}
