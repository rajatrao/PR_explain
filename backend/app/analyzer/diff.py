from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(patch: str | None) -> set[int] | None:
    """Line numbers added in the new file.

    None means the patch is missing, so the whole file counts as changed.
    """
    if patch is None:
        return None
    added: set[int] = set()
    new_line: int | None = None
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            match = _HUNK.match(raw)
            if not match:
                new_line = None
                continue
            new_line = int(match.group(1))
            continue
        if new_line is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added.add(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue
        else:
            new_line += 1
    return added


def overlaps(start: int, end: int, lines: set[int] | None) -> bool:
    if lines is None:
        return True
    if not lines:
        return False
    return any(start <= line <= end for line in lines)
