@app.post("/webhook")
async def webhook(data: dict):
    # Rimuovi eventuali link ai file audio e transcript
    if "audioFileLink" in data:
        del data["audioFileLink"]
    if "transcriptFileLink" in data:
        del data["transcriptFileLink"]

    # Qui mantieni solo trascrizione testuale
    transcription = data.get("transcription", "")

    # Poi mandi a Pipedrive quello che serve
    send_to_pipedrive(transcription, data)

    return {"status": "ok"}
