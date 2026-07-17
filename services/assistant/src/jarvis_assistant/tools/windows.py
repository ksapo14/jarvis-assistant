from __future__ import annotations

from ..config import Settings
from ..powershell import PowerShellRunner
from .base import BaseTool
from .desktop import (
    ClickNamedControlTool,
    CloseApplicationTool,
    GetActiveWindowTool,
    GetCurrentDateTimeTool,
    ListRunningApplicationsTool,
    ManageWindowTool,
    OpenApplicationTool,
    OpenWebsiteTool,
    ReadClipboardTool,
    SetClipboardTool,
    SetMuteTool,
    SetVolumeTool,
    TakeScreenshotTool,
    TypeTextTool,
)
from .files import (
    CreateFolderTool,
    DeletePathTool,
    MovePathTool,
    OpenPathTool,
    SearchFilesTool,
    WriteTextFileTool,
)
from .safe_paths import PathScope
from .system import (
    ExecuteTrustedScriptTool,
    ForceTerminateProcessTool,
    LaunchDevelopmentCommandTool,
    ManageSoftwarePackageTool,
    RunApprovedPowerShellOperationTool,
    SystemPowerActionTool,
)


def build_windows_tools(settings: Settings, powershell: PowerShellRunner) -> list[BaseTool]:
    scope = PathScope(settings.allowed_file_roots)
    screenshot_directory = settings.data_dir / "screenshots"
    return [
        GetCurrentDateTimeTool(),
        OpenApplicationTool(powershell, lambda: settings.preferred_applications),
        OpenPathTool(scope),
        OpenWebsiteTool(),
        SearchFilesTool(scope),
        GetActiveWindowTool(),
        ListRunningApplicationsTool(),
        SetVolumeTool(),
        SetMuteTool(),
        ReadClipboardTool(),
        SetClipboardTool(),
        TakeScreenshotTool(screenshot_directory),
        ManageWindowTool(),
        TypeTextTool(),
        ClickNamedControlTool(),
        CreateFolderTool(scope),
        WriteTextFileTool(scope),
        MovePathTool(scope),
        RunApprovedPowerShellOperationTool(powershell, scope),
        CloseApplicationTool(),
        LaunchDevelopmentCommandTool(
            settings.development_commands, scope, lambda: settings.developer_mode
        ),
        ExecuteTrustedScriptTool(
            settings.trusted_script_roots,
            lambda: settings.developer_mode,
            python_executable=settings.trusted_python_executable_path,
            approved_scripts=settings.trusted_script_allowlist,
            writable_roots=settings.allowed_file_roots,
        ),
        DeletePathTool(scope),
        ManageSoftwarePackageTool(),
        ForceTerminateProcessTool(),
        SystemPowerActionTool(powershell),
    ]
