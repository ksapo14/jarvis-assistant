# JARVIS assistant service

The Python 3.11+ backend for the JARVIS desktop application. It binds only to
loopback, authenticates every client, owns the audio/reasoning/action pipeline,
and enforces tool permissions independently of the language model.

From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,audio,wakeword,windows]"
$env:ASSISTANT_MOCK_MODE = "true"
jarvis-assistant
```

Real mode additionally needs `DEEPGRAM_API_KEY`, `GEMINI_API_KEY`,
`PIPER_EXECUTABLE_PATH`, and `PIPER_MODEL_PATH`. No voice or wake-word model is
bundled. Supply a licensed Piper ONNX voice and a compatible openWakeWord model.
