from fastapi import FastAPI, Request, BackgroundTasks
import asyncio, httpx, re, time
from typing import Optional, Dict, Tuple, List

app = FastAPI()

# ========= CONFIG =========
RINGOVER_API_KEY = "4dda64fbf2ac86463b995be126b906fec64b1cb2"  # tua key
API_BASE = "https://public-api.ringover.com"
HEADERS = {"Authorization": f"Bearer {RINGOVER_API_KEY}"}

# attese/ritenti per dare tempo a Ringover di allegare l'audio
INITIAL_DELAY = 90
PENDING_CHECK_PERIOD = 60

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

pending: Dict[Tuple[Optional[str], Optional[str]], Dict[str, float]] = {}

# ========= HTTP HELPERS =========
async def _delete_rec_by_id(client: httpx.AsyncClient, rec_id: str) -> bool:
    url = f"{API_BASE}/recordings/{rec_id}"
    r = await client.delete(url, headers=HEADERS, timeout=15)
    print(f"[DELETE rec] {rec_id} -> {r.status_code} {r.text[:160]}")
    return r.status_code in (200, 204, 404)  # 404 = già sparito

async def _delete_all_by_call_id(client: httpx.AsyncClient, call_id: str) -> bool:
    # Alcuni tenant non espongono questo endpoint → 404: fallimento (così proseguiamo coi GET)
    url = f"{API_BASE}/v2/calls/{call_id}/recordings"
    r = await client.delete(url, headers=HEADERS, timeout=15)
    print(f"[DELETE call/all] {call_id} -> {r.status_code} {r.text[:160]}")
    return r.status_code in (200, 204)

async def _get_call_by_id(client: httpx.AsyncClient, call_id: str):
    url = f"{API_BASE}/v2/calls/{call_id}"
    r = await client.get(url, headers=HEADERS, timeout=15)
    print(f"[GET call id] {call_id} -> {r.status_code}")
    return r.json() if r.status_code == 200 else None

async def _get_call_by_uuid(client: httpx.AsyncClient, call_uuid: str):
    # Tenant diversi espongono path diversi
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

    # campi comuni che possono contenere recording ids
    for k in ("recordings", "recording_ids", "media", "audio"):
        v = call_json.get(k)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    recs.add(it)
                elif isinstance(it, dict):
                    rid = it.get("id") or it.get("recording_id")
                    if rid:
                        recs.add(str(rid))

    # diretti
    if call_json.get("recording_id"):
        recs.add(str(call_json["recording_id"]))

    # a volte i recording stanno dentro nested "resources"/"links"
    for k in ("resources", "links", "data"):
        v = call_json.get(k)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    rid = it.get("recording_id") or it.get("id")
                    if rid:
                        recs.add(str(rid))

    return list(recs)

async def _try_delete_paths(call_id: Optional[str], call_uuid: Optional[str],
                            recording_id: Optional[str], audio_url: Optional[str]) -> bool:
    async with httpx.AsyncClient() as client:
        # 1) recording_id diretto (se mai arrivasse)
        if recording_id:
            if await _delete_rec_by_id(client, recording_id):
                return True

        # 2) estrai uuid dall'audio_url (se mai arrivasse)
        if audio_url:
            m = UUID_RE.search(audio_url)
            if m and await _delete_rec_by_id(client, m.group(0)):
                return True

        # 3) bulk da call_id (se supportato)
        if call_id:
            bulk_ok = await _delete_all_by_call_id(client, call_id)
            if bulk_ok:
                return True  # se non supportato, proseguiamo

        # 4) GET call by id → cancella tutti i recording trovati
        if call_id:
            cj = await _get_call_by_id(client, call_id)
            ids = _extract_rec_ids(cj)
            ok_any = False
            for rid in ids:
                ok_any = await _delete_rec_by_id(client, rid) or ok_any
            if ok_any:
                return True

        # 5) GET call by uuid → idem
        if call_uuid:
            cj = await _get_call_by_uuid(client, call_uuid)
            ids = _extract_rec_ids(cj)
            ok_any = False
            for rid in ids:
                ok_any = await _delete_rec_by_id(client, rid) or ok_any
            if ok_any:
                return True

        return False

# ========= SCHEDULING =========
async def _delayed_cleanup(call_id: Optional[str], call_uuid: Optional[str],
                           recording_id: Optional[str], audio_url: Optional[str]):
    await asyncio.sleep(INITIAL_DELAY)   # lascia il tempo a Ringover di agganciare l'audio
    ok = await _try_delete_paths(call_id, call_uuid, recording_id, audio_url)
    if ok:
        print("[CLEAN] done @~90s")
        return

    key = (call_id, call_uuid)
    pending[key] = {"added": time.time(), "tries": 0}
    print(f"[QUEUE] added pending {key}")

async def _process_pending_forever():
    while True:
        try:
            if pending:
                for key in list(pending.keys()):
                    call_id, call_uuid = key
                    meta = pending.get(key, {"tries": 0})
                    meta["tries"] = meta.get("tries", 0) + 1
                    pending[key] = meta

                    print(f"[RETRY] {key} try#{meta['tries']}")
                    ok = await _try_delete_paths(call_id, call_uuid, None, None)
                    if ok:
                        print(f"[CLEAN] pending {key} removed")
                        pending.pop(key, None)
                        continue
                    if meta["tries"] >= 2:   # due giri (≈3–5 min totali) e molliamo
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
    return {"status": "running", "pending": len(pending), "initial_delay": INITIAL_DELAY}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        data = {}

    call_details = data.get("call_details") or {}
    call_id      = data.get("call_id") or call_details.get("ringover_id")
    call_uuid    = data.get("call_uuid") or call_details.get("call_uuid")
    recording_id = data.get("recording_id")
    audio_url    = data.get("audio_url") or data.get("recording_url")

    print(f"[WEBHOOK] call_id={call_id} call_uuid={call_uuid} recording_id={recording_id} audio_url={audio_url}")

    background_tasks.add_task(_delayed_cleanup, call_id, call_uuid, recording_id, audio_url)
    return {"status": "ok", "queued": True}
