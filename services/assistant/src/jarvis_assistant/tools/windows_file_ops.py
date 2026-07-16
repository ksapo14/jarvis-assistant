from __future__ import annotations

import ctypes
import os
import stat
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path
from types import TracebackType
from typing import Any

from .base import ToolExecutionError, ToolValidationError

_DELETE = 0x00010000
_FILE_LIST_DIRECTORY = 0x0001
_FILE_READ_ATTRIBUTES = 0x0080
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_FILE_BEGIN = 0
_FILE_END = 2
_FILE_RENAME_INFO_CLASS = 3
_FILE_DISPOSITION_INFO_CLASS = 4
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_ERROR_NOT_SAME_DEVICE = 17
_ERROR_SHARING_VIOLATION = 32
_ERROR_FILE_EXISTS = 80
_ERROR_DIR_NOT_EMPTY = 145
_ERROR_ALREADY_EXISTS = 183


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _FileRenameInformation(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", wintypes.BOOLEAN),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]


class _FileDispositionInformation(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOLEAN)]


_kernel32: Any | None = None
if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
    _kernel32.SetEndOfFile.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL


class _WindowsHandle(AbstractContextManager["_WindowsHandle"]):
    def __init__(self, value: int) -> None:
        self.value = value

    def __enter__(self) -> _WindowsHandle:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self.value and _kernel32 is not None:
            _kernel32.CloseHandle(self.value)
            self.value = 0


def windows_path_identity(path: Path) -> dict[str, int]:
    """Capture identity from the opened object, without following the final reparse point."""
    with _open_verified(
        path,
        access=_FILE_READ_ATTRIBUTES,
        share=_FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
    ) as handle:
        return _handle_identity(handle, path)


def write_existing_text(
    path: Path,
    expected: dict[str, int],
    content: str,
    *,
    append: bool,
) -> None:
    with _open_verified(path, access=_GENERIC_WRITE | _FILE_READ_ATTRIBUTES, share=0) as handle:
        information = _get_information(handle)
        _reject_reparse_or_wrong_type(information, path, require_directory=False)
        _require_identity(handle, information, path, expected)
        _write_handle(handle, content.encode("utf-8"), append=append)


def move_path(source: Path, destination: Path, expected: dict[str, int]) -> None:
    if not destination.parent.is_dir():
        raise ToolValidationError("destination parent directory does not exist")
    with _open_verified(source, access=_DELETE | _FILE_READ_ATTRIBUTES, share=0) as source_handle:
        source_information = _get_information(source_handle)
        _reject_reparse(source_information, source)
        _require_identity(source_handle, source_information, source, expected)
        with _locked_directory_chain(destination.parent) as parent_handle:
            _rename_handle(source_handle, parent_handle, destination)


def delete_path(path: Path, expected: dict[str, int], *, recursive: bool) -> None:
    _delete_tree(path, expected=expected, recursive=recursive)


def _open_verified(path: Path, *, access: int, share: int) -> _WindowsHandle:
    kernel32 = _require_windows()
    handle = kernel32.CreateFileW(
        str(path),
        access,
        share,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        _raise_open_error(path)
    wrapped = _WindowsHandle(int(handle))
    try:
        final_path = _final_path(wrapped)
        if not _same_path(final_path, path):
            raise ToolValidationError(
                "the path resolved to a different object after confirmation; request a fresh confirmation"
            )
        return wrapped
    except BaseException:
        wrapped.__exit__(None, None, None)
        raise


@contextmanager
def _locked_directory_chain(directory: Path) -> Iterator[_WindowsHandle]:
    """Prevent replacement of every destination ancestor during absolute rename lookup."""
    anchor = Path(directory.anchor)
    if not directory.is_absolute() or not directory.anchor:
        raise ToolValidationError("destination parent must be an absolute Windows path")
    components = directory.parts[1:]
    with ExitStack() as stack:
        current = anchor
        last_handle: _WindowsHandle | None = None
        if not components:
            components = ("",)
        for component in components:
            if component:
                current /= component
            handle = stack.enter_context(
                _open_verified(
                    current,
                    access=_FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES,
                    share=_FILE_SHARE_READ | _FILE_SHARE_WRITE,
                )
            )
            _reject_reparse_or_wrong_type(_get_information(handle), current, require_directory=True)
            last_handle = handle
        assert last_handle is not None
        yield last_handle


def _get_information(handle: _WindowsHandle) -> _ByHandleFileInformation:
    kernel32 = _require_windows()
    information = _ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle.value, ctypes.byref(information)):
        _raise_mutation_error("inspect the opened file")
    return information


def _handle_identity(
    handle: _WindowsHandle,
    path: Path,
    information: _ByHandleFileInformation | None = None,
) -> dict[str, int]:
    information = information or _get_information(handle)
    _reject_reparse(information, path)
    attributes = int(information.dwFileAttributes)
    file_type = stat.S_IFDIR if attributes & _FILE_ATTRIBUTE_DIRECTORY else stat.S_IFREG
    write_time = (int(information.ftLastWriteTime.dwHighDateTime) << 32) | int(
        information.ftLastWriteTime.dwLowDateTime
    )
    return {
        "device": int(information.dwVolumeSerialNumber),
        "inode": (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
        "file_type": file_type,
        "size": (int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow),
        "mtime_ns": write_time * 100,
    }


def _require_identity(
    handle: _WindowsHandle,
    information: _ByHandleFileInformation,
    path: Path,
    expected: dict[str, int],
) -> None:
    if _handle_identity(handle, path, information) != expected:
        raise ToolValidationError(
            "the target changed after confirmation; request a fresh confirmation"
        )


def _final_path(handle: _WindowsHandle) -> Path:
    kernel32 = _require_windows()
    size = 512
    while size <= 32_768:
        buffer = ctypes.create_unicode_buffer(size)
        length = kernel32.GetFinalPathNameByHandleW(handle.value, buffer, size, 0)
        if length == 0:
            _raise_mutation_error("resolve the opened file")
        if length < size:
            value = buffer.value
            if value.startswith("\\\\?\\UNC\\"):
                value = "\\\\" + value[8:]
            elif value.startswith("\\\\?\\"):
                value = value[4:]
            return Path(value)
        size = int(length) + 1
    raise ToolValidationError("the opened path was too long to validate safely")


def _write_handle(handle: _WindowsHandle, data: bytes, *, append: bool) -> None:
    kernel32 = _require_windows()
    new_position = ctypes.c_longlong()
    move_method = _FILE_END if append else _FILE_BEGIN
    if not kernel32.SetFilePointerEx(handle.value, 0, ctypes.byref(new_position), move_method):
        _raise_mutation_error("position the text file")
    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + 1_048_576]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not kernel32.WriteFile(handle.value, buffer, len(chunk), ctypes.byref(written), None):
            _raise_mutation_error("write the text file")
        if written.value == 0:
            raise ToolExecutionError("Windows wrote zero bytes to the text file")
        offset += int(written.value)
    if not append and not kernel32.SetEndOfFile(handle.value):
        _raise_mutation_error("truncate the text file")
    if not kernel32.FlushFileBuffers(handle.value):
        _raise_mutation_error("flush the text file")


def _rename_handle(
    source_handle: _WindowsHandle,
    parent_handle: _WindowsHandle,
    destination: Path,
) -> None:
    kernel32 = _require_windows()
    destination_name = destination.name
    if not destination_name or destination_name in {".", ".."} or "\\" in destination_name:
        raise ToolValidationError("destination filename is invalid")
    # SetFileInformationByHandle requires RootDirectory=NULL. Keeping the
    # destination parent open without FILE_SHARE_DELETE prevents that directory
    # from being renamed or replaced while the absolute name is resolved.
    if not parent_handle.value:
        raise ToolExecutionError("destination directory lock closed before rename")
    encoded_name = str(destination).encode("utf-16-le")
    offset = _FileRenameInformation.FileName.offset
    # Windows validates the allocation against sizeof(FILE_RENAME_INFO), whose
    # trailing WCHAR and alignment extend past FileName.offset.
    buffer = ctypes.create_string_buffer(ctypes.sizeof(_FileRenameInformation) + len(encoded_name))
    information = ctypes.cast(buffer, ctypes.POINTER(_FileRenameInformation)).contents
    information.ReplaceIfExists = False
    information.RootDirectory = None
    information.FileNameLength = len(encoded_name)
    ctypes.memmove(ctypes.addressof(buffer) + offset, encoded_name, len(encoded_name))
    if not kernel32.SetFileInformationByHandle(
        source_handle.value,
        _FILE_RENAME_INFO_CLASS,
        buffer,
        len(buffer),
    ):
        error = ctypes.get_last_error()
        if error in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
            raise ToolValidationError(
                "destination appeared before the move committed; request a fresh confirmation"
            )
        if error == _ERROR_NOT_SAME_DEVICE:
            raise ToolValidationError("cross-volume moves are not supported safely")
        _raise_mutation_error("move the opened path", error=error)


def _delete_tree(path: Path, *, expected: dict[str, int] | None, recursive: bool) -> None:
    attributes = _get_path_attributes(path)
    hinted_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
    share = _FILE_SHARE_READ | _FILE_SHARE_WRITE if hinted_directory else 0
    access = _DELETE | _FILE_READ_ATTRIBUTES
    if hinted_directory:
        access |= _FILE_LIST_DIRECTORY
    with _open_verified(path, access=access, share=share) as handle:
        information = _get_information(handle)
        _reject_reparse(information, path)
        actual_directory = bool(int(information.dwFileAttributes) & _FILE_ATTRIBUTE_DIRECTORY)
        if actual_directory != hinted_directory:
            raise ToolValidationError(
                "the target type changed while it was being locked; request a fresh confirmation"
            )
        if expected is not None:
            _require_identity(handle, information, path, expected)
        if actual_directory:
            try:
                with os.scandir(path) as iterator:
                    children = [Path(entry.path) for entry in iterator]
            except OSError as exc:
                raise ToolExecutionError(
                    f"could not enumerate the locked directory: {exc}"
                ) from exc
            if children and not recursive:
                raise ToolValidationError("folder is not empty; recursive must be explicitly true")
            for child in children:
                _delete_tree(child, expected=None, recursive=True)
        _mark_delete(handle)


def _mark_delete(handle: _WindowsHandle) -> None:
    kernel32 = _require_windows()
    information = _FileDispositionInformation(DeleteFile=True)
    if not kernel32.SetFileInformationByHandle(
        handle.value,
        _FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        if error == _ERROR_DIR_NOT_EMPTY:
            raise ToolValidationError(
                "the directory changed during deletion and is not empty; request a fresh confirmation"
            )
        _raise_mutation_error("delete the opened path", error=error)


def _get_path_attributes(path: Path) -> int:
    kernel32 = _require_windows()
    attributes = int(kernel32.GetFileAttributesW(str(path)))
    if attributes == _INVALID_FILE_ATTRIBUTES:
        _raise_open_error(path)
    return attributes


def _reject_reparse(information: _ByHandleFileInformation, path: Path) -> None:
    if int(information.dwFileAttributes) & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise ToolValidationError(f"reparse points cannot be mutated safely: {path}")


def _reject_reparse_or_wrong_type(
    information: _ByHandleFileInformation,
    path: Path,
    *,
    require_directory: bool,
) -> None:
    _reject_reparse(information, path)
    is_directory = bool(int(information.dwFileAttributes) & _FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != require_directory:
        expected = "directory" if require_directory else "regular file"
        raise ToolValidationError(f"target is not an existing {expected}")


def _raise_open_error(path: Path) -> None:
    error = ctypes.get_last_error()
    if error in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
        raise ToolValidationError(
            "the target disappeared after confirmation; request a fresh confirmation"
        )
    if error == _ERROR_SHARING_VIOLATION:
        raise ToolValidationError(
            "the target could not be locked exclusively; close other users and confirm again"
        )
    if error == _ERROR_ACCESS_DENIED:
        raise ToolExecutionError(f"Windows denied access to the target: {path}")
    _raise_mutation_error("open the target", error=error)


def _raise_mutation_error(action: str, *, error: int | None = None) -> None:
    code = ctypes.get_last_error() if error is None else error
    detail = ctypes.FormatError(code).strip()
    raise ToolExecutionError(f"Windows could not {action}: {detail} (error {code})")


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(
        os.path.normpath(str(second))
    )


def _require_windows() -> Any:
    if _kernel32 is None:
        raise RuntimeError("Windows handle operations are unavailable")
    return _kernel32
