from __future__ import annotations

import pytest

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.powershell import (
    PowerShellOperationDenied,
    PowerShellRunner,
    PowerShellValidationError,
)


def test_unknown_or_injected_operation_name_is_rejected() -> None:
    runner = PowerShellRunner("powershell")
    with pytest.raises(PowerShellValidationError):
        runner.validate("get_processes; Remove-Item", {})
    with pytest.raises(PowerShellOperationDenied):
        runner.validate("arbitrary_shell", {})


def test_operation_arguments_are_strictly_typed() -> None:
    runner = PowerShellRunner("powershell")
    with pytest.raises(PowerShellOperationDenied):
        runner.validate("set_volume", {"level": 50})
    with pytest.raises(PowerShellValidationError):
        runner.validate("get_processes", {"limit": 10, "command": "whoami"})
    with pytest.raises(PowerShellValidationError):
        runner.validate("list_directory", {"path": "C:/safe\nRemove-Item C:/", "limit": 10})


async def test_shutdown_operation_requires_confirmation_before_spawn() -> None:
    runner = PowerShellRunner("definitely-not-executed")
    with pytest.raises(PowerShellOperationDenied, match="confirmation"):
        await runner.run(
            "shutdown_computer",
            {"action": "shutdown"},
            CancellationToken(),
            confirmed=False,
        )
