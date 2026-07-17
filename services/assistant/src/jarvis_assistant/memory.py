from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from .application_aliases import normalize_preferred_applications
from .logging_config import redact
from .models import ConversationMessage, PermissionLevel, RiskLevel, ToolResult

T = TypeVar("T")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MemoryService:
    def __init__(self, database_path: Path | str) -> None:
        self._path = database_path
        self._connection: sqlite3.Connection | None = None
        self._thread_lock = threading.RLock()
        self._async_lock = asyncio.Lock()

    async def initialize(self) -> None:
        path = str(self._path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        await self._run(self._migrate)

    def _migrate(self) -> None:
        connection = self._require_connection()
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tool_policies (
                tool_name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                permission TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_call_id TEXT,
                name TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recent_commands (
                id TEXT PRIMARY KEY,
                request TEXT NOT NULL,
                response TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tool_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id TEXT,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                confirmation_result TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS confirmations (
                id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                action_fingerprint TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                prompt TEXT NOT NULL,
                decision TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_at TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_tool_history_created ON tool_history(created_at DESC);
            CREATE INDEX IF NOT EXISTS ix_conversations_created ON conversations(created_at DESC);
            """
        )
        connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._run(self._connection.close)
            self._connection = None

    async def set_setting(self, key: str, value: Any) -> None:
        safe_value = (
            normalize_preferred_applications(value)
            if key == "preferred_applications"
            else redact(value)
        )
        value_json = json.dumps(safe_value, ensure_ascii=False)

        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                """INSERT INTO settings(key, value_json, updated_at) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                updated_at=excluded.updated_at""",
                (key, value_json, _now()),
            )
            connection.commit()

        await self._run(operation)

    async def get_setting(self, key: str, default: T | None = None) -> Any | T | None:
        def operation() -> Any | T | None:
            row = (
                self._require_connection()
                .execute("SELECT value_json FROM settings WHERE key=?", (key,))
                .fetchone()
            )
            return json.loads(row["value_json"]) if row else default

        return await self._run(operation)

    async def get_settings(self) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            rows = (
                self._require_connection()
                .execute("SELECT key, value_json FROM settings ORDER BY key")
                .fetchall()
            )
            return {row["key"]: json.loads(row["value_json"]) for row in rows}

        return await self._run(operation)

    async def set_tool_policy(
        self, tool_name: str, *, enabled: bool, permission: PermissionLevel
    ) -> None:
        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                """INSERT INTO tool_policies(tool_name, enabled, permission, updated_at)
                VALUES(?, ?, ?, ?) ON CONFLICT(tool_name) DO UPDATE SET
                enabled=excluded.enabled, permission=excluded.permission,
                updated_at=excluded.updated_at""",
                (tool_name, int(enabled), permission.value, _now()),
            )
            connection.commit()

        await self._run(operation)

    async def get_tool_policy(self, tool_name: str) -> tuple[bool, PermissionLevel] | None:
        def operation() -> tuple[bool, PermissionLevel] | None:
            row = (
                self._require_connection()
                .execute(
                    "SELECT enabled, permission FROM tool_policies WHERE tool_name=?", (tool_name,)
                )
                .fetchone()
            )
            if row is None:
                return None
            return bool(row["enabled"]), PermissionLevel(row["permission"])

        return await self._run(operation)

    async def list_tool_policies(self) -> dict[str, tuple[bool, PermissionLevel]]:
        def operation() -> dict[str, tuple[bool, PermissionLevel]]:
            rows = (
                self._require_connection()
                .execute("SELECT tool_name, enabled, permission FROM tool_policies")
                .fetchall()
            )
            return {
                row["tool_name"]: (bool(row["enabled"]), PermissionLevel(row["permission"]))
                for row in rows
            }

        return await self._run(operation)

    async def add_conversation(self, message: ConversationMessage) -> None:
        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                """INSERT INTO conversations(role, content, tool_call_id, name, created_at)
                VALUES(?, ?, ?, ?, ?)""",
                (
                    message.role.value,
                    str(redact(message.content)),
                    message.tool_call_id,
                    message.name,
                    _now(),
                ),
            )
            connection.commit()

        await self._run(operation)

    async def recent_conversation(self, limit: int = 20) -> list[ConversationMessage]:
        def operation() -> list[ConversationMessage]:
            rows = (
                self._require_connection()
                .execute(
                    """SELECT role, content, tool_call_id, name FROM conversations
                ORDER BY id DESC LIMIT ?""",
                    (limit,),
                )
                .fetchall()
            )
            return [
                ConversationMessage(
                    role=row["role"],
                    content=row["content"],
                    tool_call_id=row["tool_call_id"],
                    name=row["name"],
                )
                for row in reversed(rows)
            ]

        return await self._run(operation)

    async def set_summary(self, summary: str) -> None:
        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                "INSERT INTO conversation_summaries(summary, created_at) VALUES(?, ?)",
                (str(redact(summary)), _now()),
            )
            connection.commit()

        await self._run(operation)

    async def latest_summary(self) -> str | None:
        def operation() -> str | None:
            row = (
                self._require_connection()
                .execute("SELECT summary FROM conversation_summaries ORDER BY id DESC LIMIT 1")
                .fetchone()
            )
            return str(row["summary"]) if row else None

        return await self._run(operation)

    async def start_command(self, command_id: str, request: str) -> None:
        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                "INSERT INTO recent_commands(id, request, status, created_at) VALUES(?, ?, ?, ?)",
                (command_id, str(redact(request)), "running", _now()),
            )
            connection.commit()

        await self._run(operation)

    async def update_command_request(self, command_id: str, request: str) -> None:
        """Replace the provisional request label once voice transcription is final."""

        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                "UPDATE recent_commands SET request=? WHERE id=? AND status='running'",
                (str(redact(request)), command_id),
            )
            connection.commit()

        await self._run(operation)

    async def finish_command(self, command_id: str, response: str, status: str) -> None:
        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                """UPDATE recent_commands SET response=?, status=?, completed_at=? WHERE id=?""",
                (str(redact(response)), status, _now(), command_id),
            )
            connection.commit()

        await self._run(operation)

    async def add_tool_history(
        self,
        *,
        command_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        risk_level: RiskLevel,
        confirmation_result: str | None,
    ) -> None:
        safe_arguments = _sanitize_tool_data(tool_name, arguments)
        safe_result = _sanitize_tool_data(tool_name, result.model_dump(mode="json"))

        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                """INSERT INTO tool_history(command_id, tool_name, arguments_json, result_json,
                risk_level, confirmation_result, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (
                    command_id,
                    tool_name,
                    json.dumps(safe_arguments, ensure_ascii=False),
                    json.dumps(safe_result, ensure_ascii=False),
                    risk_level.value,
                    confirmation_result,
                    _now(),
                ),
            )
            connection.commit()

        await self._run(operation)

    async def history(self, limit: int = 100) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            connection = self._require_connection()
            tool_rows = connection.execute(
                """SELECT th.id, th.command_id, th.tool_name, th.arguments_json,
                th.result_json, th.risk_level, th.confirmation_result, th.created_at,
                rc.request, rc.response, rc.status AS command_status
                FROM tool_history AS th
                LEFT JOIN recent_commands AS rc ON rc.id = th.command_id
                ORDER BY th.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            command_rows = connection.execute(
                """SELECT -rowid AS id, id AS command_id, request, response,
                status AS command_status, created_at FROM recent_commands AS rc
                WHERE NOT EXISTS (
                    SELECT 1 FROM tool_history AS th WHERE th.command_id = rc.id
                ) AND status <> 'running' ORDER BY rowid DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            activities: list[dict[str, Any]] = []
            for row in tool_rows:
                tool_result = json.loads(row["result_json"])
                activities.append(
                    {
                        "id": row["id"],
                        "command_id": row["command_id"],
                        "created_at": row["created_at"],
                        "user_request": row["request"] or "",
                        "assistant_response": row["response"] or "",
                        "tool_name": row["tool_name"],
                        "tool_arguments": json.loads(row["arguments_json"]),
                        "tool_result": tool_result,
                        "risk_level": row["risk_level"],
                        "confirmation_result": _normalize_confirmation(row["confirmation_result"]),
                        "status": _tool_activity_status(tool_result, row["command_status"]),
                    }
                )
            for row in command_rows:
                activities.append(
                    {
                        "id": row["id"],
                        "command_id": row["command_id"],
                        "created_at": row["created_at"],
                        "user_request": row["request"] or "",
                        "assistant_response": row["response"] or "",
                        "status": _command_activity_status(row["command_status"]),
                    }
                )
            activities.sort(key=lambda item: str(item["created_at"]), reverse=True)
            return activities[:limit]

        return await self._run(operation)

    async def record_confirmation(
        self,
        *,
        confirmation_id: str,
        tool_name: str,
        action_fingerprint: str,
        token_hash: str,
        prompt: str,
        expires_at: str,
    ) -> None:
        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                """INSERT INTO confirmations(id, tool_name, action_fingerprint, token_hash,
                prompt, expires_at, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (
                    confirmation_id,
                    tool_name,
                    action_fingerprint,
                    token_hash,
                    str(redact(prompt)),
                    expires_at,
                    _now(),
                ),
            )
            connection.commit()

        await self._run(operation)

    async def record_confirmation_decision(self, confirmation_id: str, decision: str) -> None:
        def operation() -> None:
            connection = self._require_connection()
            connection.execute(
                "UPDATE confirmations SET decision=?, decided_at=? WHERE id=?",
                (decision, _now(), confirmation_id),
            )
            connection.commit()

        await self._run(operation)

    async def clear_local_data(self) -> None:
        def operation() -> None:
            connection = self._require_connection()
            connection.execute("PRAGMA secure_delete=ON")
            for table in (
                "settings",
                "tool_policies",
                "conversations",
                "conversation_summaries",
                "recent_commands",
                "tool_history",
                "confirmations",
            ):
                connection.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed allowlist
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA secure_delete=FAST")

        await self._run(operation)

    async def _run(self, operation: Callable[[], T]) -> T:
        async with self._async_lock:
            return await asyncio.to_thread(self._locked_operation, operation)

    def _locked_operation(self, operation: Callable[[], T]) -> T:
        with self._thread_lock:
            return operation()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("memory service is not initialized")
        return self._connection


def _sanitize_tool_data(tool_name: str, data: dict[str, Any]) -> dict[str, Any]:
    if tool_name in {"read_clipboard", "set_clipboard", "type_text", "write_text_file"}:
        return {
            key: "[REDACTED]" if key in {"text", "content", "data"} else redact(value, key=key)
            for key, value in data.items()
        }
    return redact(data)


def _normalize_confirmation(value: str | None) -> str | None:
    return {
        "yes": "approved",
        "no": "denied",
        "denied": "denied",
        "expired": "expired",
        "cancelled": "cancelled",
    }.get(value, value)


def _command_activity_status(value: str | None) -> str:
    if value == "completed":
        return "success"
    if value == "cancelled":
        return "cancelled"
    return "error"


def _tool_activity_status(result: dict[str, Any], command_status: str | None) -> str:
    if command_status == "cancelled":
        return "cancelled"
    if bool(result.get("success")):
        return "success"
    if result.get("error_code") in {
        "confirmation_declined",
        "confirmation_required",
        "permission_changed",
        "permission_denied",
    }:
        return "denied"
    return "error"
