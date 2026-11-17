"""How a backend's SSE events become text. Chosen per backend with `sse:` in backends.yaml.

auto   plain text, or JSON with the text under content/text/delta/answer/message/value
       (or under the backend's `sseField`). "[DONE]" and events without text are skipped.
typed  events that name their kind: {"key": ..., "type": "streaming", "value": "Hello"}.
       "streaming" values are the answer, "followup" values are suggested next questions,
       everything else (start, status, end) is progress chatter and is dropped.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from httpx_sse import ServerSentEvent

from .config import BackendSettings

_TEXT_KEYS = ("content", "text", "delta", "answer", "message", "value")


@dataclass
class SseItem:
    kind: str  # "chunk" or "followup"
    value: Any


@dataclass
class StreamResult:
    text: str = ""
    followups: list[Any] = field(default_factory=list)


def default_sse_extract(event: ServerSentEvent, text_field: str | None = None) -> str | None:
    """One "auto" event as a text chunk (None = skip)."""
    data = event.data
    if not data or data.strip() == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except ValueError:
        return data
    if isinstance(payload, dict):
        for key in (text_field,) if text_field else _TEXT_KEYS:
            if isinstance(payload.get(key), str):
                return payload[key]
        return None
    return str(payload)


def _typed(event: ServerSentEvent) -> list[SseItem]:
    try:
        payload = json.loads(event.data)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    kind, value = payload.get("type"), payload.get("value")
    if kind == "streaming" and value not in (None, ""):
        return [SseItem("chunk", value if isinstance(value, str) else json.dumps(value))]
    if kind == "followup" and value is not None:
        if isinstance(value, str):  # some backends send the list JSON-encoded in a string
            try:
                decoded = json.loads(value)
            except ValueError:
                decoded = value
            value = decoded if isinstance(decoded, list) else value
        return [SseItem("followup", item) for item in (value if isinstance(value, list) else [value])]
    return []


def parse_event(event: ServerSentEvent, cfg: BackendSettings) -> list[SseItem]:
    if cfg.sse == "typed":
        return _typed(event)
    chunk = default_sse_extract(event, cfg.sse_field)
    return [SseItem("chunk", chunk)] if chunk else []
