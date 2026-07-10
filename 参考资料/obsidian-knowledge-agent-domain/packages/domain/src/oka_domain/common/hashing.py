from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from .value_objects import Sha256Hash


def to_primitive(value: Any, *, exclude_fields: frozenset[str] = frozenset()) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): to_primitive(item, exclude_fields=exclude_fields)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        converted = [to_primitive(item, exclude_fields=exclude_fields) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [to_primitive(item, exclude_fields=exclude_fields) for item in value]
    if is_dataclass(value):
        result: dict[str, Any] = {}
        for field in fields(value):
            if field.name.startswith("_") or field.name in exclude_fields:
                continue
            result[field.name] = to_primitive(
                getattr(value, field.name), exclude_fields=exclude_fields
            )
        return result
    if hasattr(value, "value"):
        return to_primitive(value.value, exclude_fields=exclude_fields)
    raise TypeError(f"Cannot serialize {type(value)!r} into a canonical primitive")


def canonical_json(value: Any, *, exclude_fields: frozenset[str] = frozenset()) -> str:
    return json.dumps(
        to_primitive(value, exclude_fields=exclude_fields),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_of(value: Any, *, exclude_fields: frozenset[str] = frozenset()) -> Sha256Hash:
    data = canonical_json(value, exclude_fields=exclude_fields).encode("utf-8")
    return Sha256Hash(hashlib.sha256(data).hexdigest())


def sha256_text(value: str) -> Sha256Hash:
    return Sha256Hash(hashlib.sha256(value.encode("utf-8")).hexdigest())
