from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

_KNOWN_FOLDER_IDS = {
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
}


def default_user_file_roots(
    home: Path | None = None,
    *,
    windows: bool | None = None,
) -> tuple[Path, ...]:
    """Return redirected Windows known folders, with portable per-folder fallbacks."""
    home = (home or Path.home()).expanduser()
    use_known_folders = sys.platform == "win32" if windows is None else windows
    roots: list[Path] = []
    for name, folder_id in _KNOWN_FOLDER_IDS.items():
        fallback = home / name
        if use_known_folders:
            try:
                candidate = _known_folder_path(folder_id)
            except OSError:
                candidate = fallback
        else:
            candidate = fallback
        resolved = candidate.expanduser().resolve(strict=False)
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _known_folder_path(folder_id: str) -> Path:
    """Resolve one FOLDERID through SHGetKnownFolderPath."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    value = UUID(folder_id)
    guid = GUID(
        value.time_low,
        value.time_mid,
        value.time_hi_version,
        (ctypes.c_ubyte * 8).from_buffer_copy(value.bytes[8:]),
    )
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    raw_path = ctypes.c_wchar_p()
    result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(raw_path))
    if result != 0 or not raw_path.value:
        raise OSError(f"SHGetKnownFolderPath failed with HRESULT 0x{result & 0xFFFFFFFF:08X}")
    try:
        return Path(raw_path.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(raw_path, ctypes.c_void_p))
