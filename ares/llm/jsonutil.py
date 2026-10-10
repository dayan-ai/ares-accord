"""Tolerant JSON extraction for LLM replies.

Model output is treated strictly as data: it is parsed with ``json.loads`` only
(never eval/exec). Handles code fences, leading/trailing prose, trailing commas,
smart quotes and Python-style literals. Raises ``LLMJSONError`` when no JSON
object can be recovered.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ares.llm.base import LLMJSONError

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _balanced_objects(text: str) -> list[str]:
    """Return every top-level {...} substring, respecting JSON strings."""
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(text[start:i + 1])
                start = -1
    if depth > 0 and start >= 0:          # unterminated object: try closing it
        out.append(text[start:] + "}" * depth)
    return out


def _repair(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = _TRAILING_COMMA_RE.sub(r"\1", s)
    s = re.sub(r"(?<![\"\w])True(?![\"\w])", "true", s)
    s = re.sub(r"(?<![\"\w])False(?![\"\w])", "false", s)
    s = re.sub(r"(?<![\"\w])None(?![\"\w])", "null", s)
    return s


def _try_load(s: str) -> dict[str, Any] | None:
    for candidate in (s, _repair(s)):
        try:
            val = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(val, dict):
            return val
        if isinstance(val, list) and len(val) == 1 and isinstance(val[0], dict):
            return val[0]
    return None


def extract_json(text: Any) -> dict[str, Any]:
    """Extract a single JSON object from an LLM reply or raise LLMJSONError."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip():
        raise LLMJSONError("empty model reply")
    s = text.strip()
    found = _try_load(s)
    if found is not None:
        return found
    pieces = [m.group(1).strip() for m in _FENCE_RE.finditer(s)]
    for piece in pieces:
        found = _try_load(piece)
        if found is not None:
            return found
    for source in pieces + [s]:
        for obj in _balanced_objects(source):
            found = _try_load(obj)
            if found is not None:
                return found
    raise LLMJSONError("model reply contained no parseable JSON object")
