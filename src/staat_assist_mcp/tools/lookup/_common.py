"""Shared by the lookup tools."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...config import get_settings

TAGS = {"lookup"}
READ_ONLY = {"readOnlyHint": True}


@lru_cache
def _splits_by_user() -> dict[str, list[str]]:
    """user id (lower case) -> splits, from the user splits file. Read once; restart to pick up changes."""
    entries = json.loads(Path(get_settings().user_splits_file).read_text(encoding="utf-8"))
    return {entry["userid"].lower(): entry.get("splits") or [] for entry in entries}


def allowed_splits(user_id: str | None) -> list[str]:
    """The splits (prd_ow_i) the user may see; empty when the user is unknown."""
    return _splits_by_user().get(user_id.lower(), []) if user_id else []


def compact(value: Any) -> Any:
    """Drop null and empty fields, recursively: keeps tool results small for the LLM."""
    if isinstance(value, dict):
        cleaned = {key: compact(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item not in (None, "", [], {})}
    if isinstance(value, list):
        return [compact(item) for item in value]
    return value
