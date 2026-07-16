from __future__ import annotations

from dataclasses import dataclass

from .memory import MemoryService
from .models import PermissionLevel, RiskLevel, ToolDescriptor


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str


def default_permission(risk_level: RiskLevel, confirmation_required: bool) -> PermissionLevel:
    if risk_level is RiskLevel.LOW and not confirmation_required:
        return PermissionLevel.ALWAYS_ALLOW
    return PermissionLevel.ASK_EVERY_TIME


class PermissionManager:
    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory
        self._session_grants: set[str] = set()

    async def policy_for(self, descriptor: ToolDescriptor) -> tuple[bool, PermissionLevel]:
        saved = await self._memory.get_tool_policy(descriptor.name)
        if saved is not None:
            return saved
        return descriptor.enabled, default_permission(
            descriptor.risk_level, descriptor.confirmation_required
        )

    async def update_policy(
        self,
        descriptor: ToolDescriptor,
        *,
        enabled: bool | None = None,
        permission: PermissionLevel | None = None,
    ) -> tuple[bool, PermissionLevel]:
        current_enabled, current_permission = await self.policy_for(descriptor)
        next_enabled = current_enabled if enabled is None else enabled
        next_permission = current_permission if permission is None else permission
        if (
            descriptor.risk_level is RiskLevel.HIGH
            and next_permission is PermissionLevel.ALWAYS_ALLOW
        ):
            raise ValueError("high-risk tools cannot be permanently auto-approved")
        await self._memory.set_tool_policy(
            descriptor.name, enabled=next_enabled, permission=next_permission
        )
        if not next_enabled or next_permission is not PermissionLevel.ALLOW_SESSION:
            self._session_grants.discard(descriptor.name)
        return next_enabled, next_permission

    async def authorize(self, descriptor: ToolDescriptor) -> AuthorizationDecision:
        enabled, permission = await self.policy_for(descriptor)
        if not enabled or permission is PermissionLevel.DISABLED:
            return AuthorizationDecision(False, False, "tool is disabled")
        if descriptor.risk_level is RiskLevel.HIGH:
            return AuthorizationDecision(
                True, True, "high-risk actions always require confirmation"
            )
        if descriptor.confirmation_required:
            return AuthorizationDecision(
                True, True, "tool policy requires confirmation for every action"
            )
        if permission is PermissionLevel.ASK_EVERY_TIME:
            return AuthorizationDecision(True, True, "permission is ask every time")
        if (
            permission is PermissionLevel.ALLOW_SESSION
            and descriptor.name not in self._session_grants
        ):
            return AuthorizationDecision(True, True, "session permission has not been granted yet")
        return AuthorizationDecision(True, False, "permission granted")

    def grant_for_session(self, tool_name: str) -> None:
        self._session_grants.add(tool_name)

    def clear_session(self) -> None:
        self._session_grants.clear()
