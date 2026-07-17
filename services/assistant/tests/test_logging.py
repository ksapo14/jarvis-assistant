from __future__ import annotations

import json
import logging

from jarvis_assistant.logging_config import JsonFormatter, redact


def test_recursive_redaction_covers_tokens_and_clipboard() -> None:
    value = {
        "api_key": "secret",
        "nested": {"authorization": "Bearer abcdefghijklmnop"},
        "clipboard": "private text",
    }
    result = redact(value)
    assert result["api_key"] == "[REDACTED]"
    assert result["nested"]["authorization"] == "[REDACTED]"
    assert result["clipboard"] == "[REDACTED]"


def test_json_formatter_emits_structured_record_without_secret() -> None:
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "Token abcdefghijklmnop", (), None
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert "abcdefghijklmnop" not in payload["message"]
