from fastapi import FastAPI, Request
app = FastAPI()

@app.get("/")
async def health():
    return {"status": "running", "mode": "neutral"}

@app.post("/webhook")
async def webhook(request: Request):
    # NON fa niente, lascia lavorare Empower
    return {"status": "ok", "action": "none"}
