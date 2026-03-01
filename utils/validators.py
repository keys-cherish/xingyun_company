"""Name and input validation helpers."""

from __future__ import annotations

import re

_FORBIDDEN_NAME_PREFIX = ("/", "@")
_FORBIDDEN_CHARS = re.compile(r"[\n\r\t]")
_PURE_NUMERIC = re.compile(r"^\d+$")


def validate_name(name: str, min_len: int = 1, max_len: int = 32) -> str | None:
    """Validate a user-supplied name.

    Returns an error message string if invalid, or ``None`` if the name is
    acceptable.
    """
    if not name or len(name) < min_len:
        return f"名称至少{min_len}个字符"
    if len(name) > max_len:
        return f"名称最长{max_len}个字符"
    if any(name.startswith(p) for p in _FORBIDDEN_NAME_PREFIX):
        return "名称不能以 / 或 @ 开头"
    if _FORBIDDEN_CHARS.search(name):
        return "名称不能包含换行或特殊控制字符"
    if _PURE_NUMERIC.match(name):
        return "名称不能为纯数字"
    return None
