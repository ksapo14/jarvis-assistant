# PyInstaller specification for the production Python sidecar.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
SOURCE = ROOT / "services" / "assistant" / "src"

datas = []
binaries = []
hiddenimports = [
    *collect_submodules("uvicorn"),
    *collect_submodules("jarvis_assistant"),
    "sounddevice",
]
hiddenimports.extend(["openwakeword.model", "openwakeword.utils", "openwakeword.vad"])

analysis = Analysis(
    [str(SOURCE / "jarvis_assistant" / "__main__.py")],
    pathex=[str(SOURCE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="jarvis-assistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
