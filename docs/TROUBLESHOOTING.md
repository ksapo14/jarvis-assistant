# Troubleshooting

Start with mock mode. If mock mode works, the desktop protocol, state machine, registry, confirmation flow, SQLite, and UI are intact; investigate the selected hardware/provider next.

```powershell
.\scripts\dev.ps1 -Mock
.\scripts\test.ps1
```

## Setup and build

### Python is the wrong version or architecture

Run:

```powershell
python --version
python -c "import platform,sys; print(sys.executable, platform.machine())"
```

Python 3.11+ is required. CPython x64 is recommended on standard x64 Windows. Remove `.venv` only after confirming it belongs to this repository, then rerun setup with the intended Python launcher.

### A Windows Python wheel will not install

`sounddevice`, `onnxruntime`, Piper, pywin32, or other audio/Windows extras may not publish every Python/architecture combination simultaneously. Use a supported CPython x64 release, update pip, and retry:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip wheel
.\scripts\setup.ps1
```

Mock-only setup avoids hardware extras:

```powershell
.\scripts\setup.ps1 -MockOnly
```

### `cargo` or `rustc` is not recognized

Install [rustup](https://rustup.rs/), choose the stable MSVC toolchain, close/reopen PowerShell, then check:

```powershell
rustc --version
cargo --version
```

Use `-WebOnly` until Rust is available.

### `link.exe` or a Windows SDK library is missing

Install Microsoft C++ Build Tools and select **Desktop development with C++**, the Windows SDK, and MSVC toolset. Reopen the terminal after installation.

### WebView2 errors or blank Tauri window

Install/repair the Evergreen WebView2 Runtime. Confirm the React build separately:

```powershell
npm run desktop:build
.\scripts\dev.ps1 -Mock -WebOnly
```

### Vite port 1420 or an explicit backend port is in use

Find the owner before stopping anything:

```powershell
Get-NetTCPConnection -LocalPort 1420,8765 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess
```

Quit JARVIS from the tray. Do not force-kill an unknown process. The backend instance lock also prevents two assistant services sharing storage.

### Installer sidecar not found

Always use `scripts/build.ps1`; a direct `tauri build` does not create/stage the Python executable. Confirm a target-triple file exists under `apps\desktop\src-tauri\binaries` and that PyInstaller produced `services\assistant\dist\jarvis-assistant.exe`.

## Backend connection

### UI says “Backend disconnected”

1. Quit from the tray and restart once.
2. Run mock web-only mode to expose backend diagnostics.
3. Inspect rotating logs in the configured/default user data directory.
4. If `ASSISTANT_PORT` is explicitly set, check whether another process owns it. Packaged launches normally use an OS-assigned ephemeral port and do not have a fixed `8765` dependency.
5. Verify `ASSISTANT_HOST` is `127.0.0.1` and the session token has at least 32 random characters.

The frontend deliberately does not accept an unauthenticated backend. Do not work around this by removing the token header or putting a token in a URL.

The packaged host also refuses to expose connection details until the backend readiness file has the matching one-time nonce, a runtime that is either the spawned process or its direct child, a valid port, and the authenticated health check succeeds. `ASSISTANT_READY_FILE` and `ASSISTANT_READY_NONCE` are internal parent/child handshake variables; do not configure them in `.env`.

### HTTP returns 401/403

The backend and frontend were launched with different session tokens. Use `scripts/dev.ps1`, which generates and passes one token to both. A manually started web UI needs the same `VITE_ASSISTANT_SESSION_TOKEN` present when Vite starts.

### WebSocket connects then closes

The first frame must be `{ "type": "authenticate", "token": "..." }` within five seconds. Query-string authentication is intentionally unsupported.

## Microphone and wake phrase

### No microphone appears

- Open Windows **Settings → Privacy & security → Microphone** and allow desktop apps.
- Confirm the device is enabled in **System → Sound → Input**.
- Close apps holding exclusive access.
- List devices from the provider status/UI after restarting the backend.
- Test push-to-talk with the system default device.

### Wake word never activates

- Confirm wake detection is enabled and not paused/muted in the tray.
- Use “Hey Jarvis,” not bare “Jarvis,” with the stock model.
- Run `scripts/install-wake-model.ps1 -AcceptModelLicense` and configure all three paths it prints. Packaged builds intentionally contain no ONNX/TFLite model assets.
- Confirm the wake, melspectrogram, and embedding paths exist and use one matching openWakeWord format.
- Lower the threshold gradually; do not jump to an extremely permissive value.
- Test in a quiet room near the selected microphone.
- Remember wake detection intentionally pauses during speech playback and active turns.

If the model is missing, JARVIS reports it and leaves push-to-talk enabled.

### False activations

Raise sensitivity threshold gradually, move the microphone away from speakers/television, increase cooldown, and use a custom model tuned to the desired phrase/accent. Acoustic echo cancellation is hardware/environment dependent.

### Listening never ends

Deepgram endpointing waits for final speech/silence. Check network stability and input noise. Use Cancel; cancellation closes capture and provider tasks. Adjust endpoint/silence settings only within documented bounds.

## Deepgram

### Missing or invalid key

Set `DEEPGRAM_API_KEY` in `.env` for development, restart the entire backend, and inspect provider status. The UI never displays the full key.

### 401/403

Create/verify an active Deepgram key with transcription permissions. Remove accidental whitespace/quotes. Do not log the value.

### 429/quota exhausted

The adapter surfaces a quota/rate-limit error and returns idle; it does not retry forever. Review the Deepgram project limits/billing, wait for the reset, or switch to mock/Willow WIS.

### Network failure

Post-wake transcription needs connectivity. Pre-wake detection still remains local. Cancel the turn, verify TLS/proxy/firewall rules, and retry. Corporate intercepting proxies may need organization-approved trust configuration; do not disable certificate validation.

## Gemini

### Provider not configured

Set `GEMINI_API_KEY` and restart. Confirm `GEMINI_MODEL` names a model available to the key/project.

### Rate limit or quota error

Bounded retry applies to transient failures. Continued failures are shown plainly and audited without secrets. Review Google AI project quota or use mock mode.

### Malformed/unknown tool call

The backend rejects it by design. The activity/error log should identify the tool/schema category without executing anything. If a legitimate tool changed, update its Pydantic schema, tests, and declaration together rather than weakening validation.

### Gemini claims an action succeeded when it did not

Treat this as a bug. The provider must receive a structured tool result before producing success language. Capture redacted event/tool-result logs, reproduce in mock/provider tests, and do not add prompt wording as the only fix—preserve the executor boundary.

## Piper

### Piper executable missing

```powershell
.\scripts\install-piper.ps1
Test-Path $env:PIPER_EXECUTABLE_PATH
```

Restart after setting the path. The backend keeps text responses available even when TTS cannot run.

### Voice model missing or incompatible

Confirm both `voice.onnx` and usually `voice.onnx.json` exist, are compatible with the installed Piper release, and are readable. Do not rename metadata independently. Review the voice's license.

### No audio or wrong volume/rate

Check Windows output device/mixer, UI mute, tray mute, speech volume, and rate. Try Piper directly with its documented CLI. Wake detection remains paused until the failed playback task unwinds; Cancel should interrupt it.

## Desktop tools

### Application/file not found

Use an absolute literal path or configure a preferred application alias. Search/open tools do not expand arbitrary shell syntax or wildcards.

### Permission denied

Check the tool's enabled flag and permission setting, then Windows ACLs. JARVIS does not bypass ACLs or elevate itself.

### Cannot control an elevated window

Windows blocks lower-integrity UI Automation/Win32 messages to elevated targets. Use a non-elevated instance of the target if appropriate. JARVIS intentionally does not auto-elevate.

### Named control not found

The visible label/accessibility name may differ from displayed text, the control may be virtualized, or the app may not expose UIA. Bring the intended window forward and retry with its exact accessible name. Coordinate clicks are not an automatic fallback.

### Tool is disabled or always asks

Open **Tools & permissions**. High-risk tools always ask even if a client attempts `always_allow`. Session grants disappear when the backend exits.

### Confirmation expired or was rejected as changed

Submit a fresh request. The exact tool/arguments digest, resource state, expiry, and single-use ID are checked to prevent stale approval.

## Local data and logs

### Where is data stored?

By default, the backend uses a platform-specific per-user application data directory. Set `ASSISTANT_DATA_DIR` to an absolute user-writable directory for development/test isolation.

### History is empty

Enable **Save conversation history**. Cancelled/failed actions may be recorded as audit entries even when no successful conversation is saved. Mock tests use temporary databases.

### Clear local data

Use **Settings → Clear local data** or the authenticated delete endpoint. This resets SQLite data, rotated logs, and app-owned screenshots; the desktop also disables startup registration. It does not delete API keys from environment/Credential Manager or external Piper/wake model files.

### Logs contain something sensitive

Stop live testing, preserve only a secured/redacted sample, and report the field/event path. Extend the recursive redactor and add a regression test. Do not publish the original log in an issue.

## Collecting a safe diagnostic report

Include:

- Windows edition/build, CPU architecture, Python/Node/Rust versions.
- Mock vs live mode and provider names (never keys).
- The failing state transition and error code.
- Tool name/risk and redacted argument shapes—not private content.
- Test command output.
- A short redacted log window around the failure.

Exclude API keys, auth/session tokens, passwords, clipboard contents, private file contents, raw audio, and credential-bearing URLs.
