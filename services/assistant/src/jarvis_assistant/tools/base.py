from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ..cancellation import CancellationToken
from ..models import PermissionCategory, RiskLevel, ToolCall, ToolDescriptor


class ToolError(RuntimeError):
    code = "tool_error"


class UnknownToolError(ToolError):
    code = "unknown_tool"


class ToolValidationError(ToolError):
    code = "tool_validation_failed"


class ToolExecutionError(ToolError):
    code = "tool_execution_failed"


class ToolUnavailableError(ToolExecutionError):
    code = "tool_unavailable"


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str


class BaseTool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    permission_category: ClassVar[PermissionCategory]
    risk_level: ClassVar[RiskLevel]
    confirmation_required: ClassVar[bool] = False
    timeout_seconds: ClassVar[float] = 10.0
    enforce_timeout: ClassVar[bool] = True
    arguments_model: ClassVar[type[BaseModel]]
    result_model: ClassVar[type[BaseModel]] = ToolOutput

    @property
    def descriptor(self) -> ToolDescriptor:
        argument_schema = self.arguments_model.model_json_schema()
        _remove_internal_schema_fields(argument_schema)
        return ToolDescriptor(
            name=self.name,
            description=self.description,
            argument_schema=argument_schema,
            result_schema=self.result_model.model_json_schema(),
            permission_category=self.permission_category,
            risk_level=self.risk_level,
            confirmation_required=self.confirmation_required,
            timeout_seconds=self.timeout_seconds,
        )

    def validate(self, arguments: dict[str, object]) -> BaseModel:
        try:
            return self.arguments_model.model_validate(arguments)
        except ValidationError as exc:
            issues = []
            for error in exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ):
                location = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
                issues.append(f"{location}: {error.get('type', 'invalid')}")
            detail = "; ".join(issues[:5]) or "arguments: invalid"
            raise ToolValidationError(f"invalid arguments for {self.name}: {detail}") from exc

    def preview(self, arguments: BaseModel) -> str:
        pairs = ", ".join(
            f"{key}={value!r}" for key, value in arguments.model_dump(mode="json").items()
        )
        return f"Run {self.name} with {pairs or 'no arguments'}?"

    async def bind_confirmation(
        self,
        call: ToolCall,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> tuple[ToolCall, BaseModel]:
        """Bind volatile execution context before an exact-action confirmation."""
        cancellation.raise_if_cancelled()
        return call, arguments

    @abstractmethod
    async def execute(
        self, arguments: BaseModel, cancellation: CancellationToken
    ) -> BaseModel | dict[str, object]:
        pass


def _remove_internal_schema_fields(schema: dict[str, object]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    internal_names = [
        name
        for name, value in properties.items()
        if isinstance(value, dict) and value.get("internal") is True
    ]
    for name in internal_names:
        properties.pop(name, None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [name for name in required if name not in internal_names]
