from __future__ import annotations

import asyncio
from time import perf_counter

from pydantic import BaseModel

from ..cancellation import CancellationToken, OperationCancelled
from ..models import ToolCall, ToolDescriptor, ToolResult
from .base import (
    BaseTool,
    ToolError,
    ToolExecutionError,
    UnknownToolError,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool is already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool: {name}") from exc

    def descriptors(self) -> list[ToolDescriptor]:
        return [tool.descriptor for tool in self._tools.values()]

    def validate(self, call: ToolCall) -> tuple[BaseTool, BaseModel]:
        tool = self.get(call.name)
        return tool, tool.validate(call.arguments)

    async def execute(
        self,
        call: ToolCall,
        cancellation: CancellationToken,
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        started = perf_counter()
        try:
            tool, arguments = self.validate(call)
            if (tool.confirmation_required or tool.risk_level.value == "high") and not confirmed:
                return ToolResult(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    success=False,
                    summary="This tool requires explicit confirmation.",
                    error_code="confirmation_required",
                    duration_ms=int((perf_counter() - started) * 1000),
                )
            cancellation.raise_if_cancelled()
            execution = tool.execute(arguments, cancellation)
            raw_result = (
                await asyncio.wait_for(execution, timeout=tool.timeout_seconds)
                if tool.enforce_timeout
                else await execution
            )
            result = tool.result_model.model_validate(raw_result)
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=True,
                summary=str(getattr(result, "message", "Tool completed")),
                data=result.model_dump(mode="json"),
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except OperationCancelled:
            raise
        except TimeoutError:
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                summary="The tool timed out.",
                error_code="tool_timeout",
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except ToolError as exc:
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                summary=str(exc),
                error_code=exc.code,
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except Exception:
            return ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                summary="Tool execution failed unexpectedly.",
                error_code=ToolExecutionError.code,
                duration_ms=int((perf_counter() - started) * 1000),
            )
