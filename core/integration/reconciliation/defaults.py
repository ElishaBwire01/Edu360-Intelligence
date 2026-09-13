"""Normalization helpers shared by reconciliation strategies."""

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Mapping


def value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        current = record.get(key)
        if current not in (None, ""):
            return current
    return None


def normalized_text(raw: Any) -> str:
    text = unicodedata.normalize("NFKC", str(raw or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalized_date(raw: Any) -> str:
    if isinstance(raw, (date, datetime)):
        return raw.isoformat()
    return normalized_text(raw)