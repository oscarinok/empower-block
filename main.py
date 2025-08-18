from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
async def health():
    return {"status": "running", "mode": "neutral"}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("Webhook ricevuto:", data)  # Lo vedi nei log Render
    return {"status": "ok", "action": "none"}
