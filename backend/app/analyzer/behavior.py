"""Old-versus-new behavior facts read from the compare patch.

The patch already carries both sides of every hunk: ``-`` lines are the base
behavior and ``+`` lines are the head behavior. This module groups those lines
by the head function they land in, classifies each statement (signature,
return, error, condition, value, call, logging), pairs a removed statement with
the added statement it most likely replaced, and reports each pair as one
deterministic delta. No model is involved and nothing is inferred beyond the
two sides of the diff.
"""

from __future__ import annotations

import difflib
import posixpath
import re
from dataclasses import dataclass, field

from app.analyzer.types import FileChange, Symbol

TEXT_CAP = 220
MAX_DELTAS_PER_SYMBOL = 8
MAX_SYMBOLS = 40

# Lower sorts first: what a caller is most likely to observe.
CATEGORY_ORDER = {
    "removed_function": 0,
    "signature": 1,
    "error": 2,
    "return": 3,
    "condition": 4,
    "value": 5,
    "call": 6,
    "logging": 7,
    "logic": 8,
}

CATEGORY_LABEL = {
    "removed_function": "Function removed",
    "signature": "Inputs",
    "error": "Errors",
    "return": "Returns",
    "condition": "Condition",
    "value": "Value",
    "call": "Calls",
    "logging": "Logging",
    "logic": "Logic",
}

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_HEADER = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?def\s+(?P<py>\w+)\s*\("
    r"|^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<js>\w+)\s*[<(]"
    r"|^func\s+(?:\([^)]*\)\s*)?(?P<go>\w+)\s*[\[(]"
    r"|^(?:export\s+)?(?:const|let|var)\s+(?P<arrow>\w+)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*(?::[^=]+)?=>"
    r"|^(?:(?:public|private|protected|static|final|abstract|synchronized|async|override|readonly)\s+)+"
    r"(?:[\w<>\[\],.?]+\s+)?(?P<method>\w+)\s*\([^;]*$"
)
_RETURN = re.compile(r"^(?:return|yield)\b")
_ERROR = re.compile(
    r"^(?:raise|throw)\b|^(?:except|catch)\b|^\}\s*catch\b|\bpanic\(|\berrors\.New\(|\bfmt\.Errorf\("
    r"|\bHTTPException\(|\babort\(|^return\s+.*\berr\b"
)
_CONDITION = re.compile(
    r"^(?:\}\s*)?(?:if|elif|else\s+if|while|for|switch|case|match|when|unless|guard)\b|^.*\?.*:.*$(?<!:)"
)
_LOGGING = re.compile(
    r"^(?:console\.\w+|print|println|logger\.\w+|logging\.\w+|log\.\w+|self\.log\w*\.\w+|fmt\.Print\w*|System\.(?:out|err)\.print\w*)\s*\("
)
_ASSIGN = re.compile(
    r"^(?:(?:const|let|var|final|val|static|readonly|private|public|protected)\s+)*"
    r"(?:[\w<>\[\],.?]+\s+)?(?P<lhs>[A-Za-z_][\w.]*(?:\[[^\]]+\])?)\s*(?::\s*[^=]+)?\s*(?::=|(?<![=!<>+\-*/%&|^])=(?!=))\s*(?P<rhs>.+)$"
)
_KEYED = re.compile(r"^[\"']?(?P<lhs>[A-Za-z_][\w-]*)[\"']?\s*:\s*(?P<rhs>[^:].*)$")
_CALL_NAME = re.compile(r"(?<![\w.])((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*)\s*\(")
_NOT_CALLS = {
    "if", "elif", "while", "for", "switch", "return", "yield", "catch", "except", "function", "def", "func",
    "await", "new", "typeof", "sizeof", "print", "raise", "throw", "super", "this", "self", "lambda", "and", "or", "not",
    "isinstance", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "String", "Number", "Boolean",
}
# `raise X(...)` / `throw new X(...)` / `new X(...)` build a value; they are not calls into code.
_CONSTRUCTED = re.compile(r"(?:\braise|\bthrow(?:\s+new)?|\bnew)\s*$")
_TRIVIAL = re.compile(r"^(?:[{}()\[\];,]+|else\s*:?|else\s*\{|\}\s*else\s*\{|pass|break|continue|end|\*/|/\*\*?|\"\"\"|''')$")
_COMMENT = re.compile(r"^(?:#|//|/\*|\*|--)")
_IMPORT = re.compile(r"^(?:import\b|from\s+\S+\s+import\b|package\b|using\b|#include\b|require\()")
_IDENT = re.compile(r"^[A-Za-z_]\w*$")


@dataclass
class _Line:
    old: int | None
    new: int | None
    text: str
    kind: str = "logic"
    key: str | None = None
    consumed: bool = False


@dataclass
class _Block:
    anchor: int
    removed: list[_Line] = field(default_factory=list)
    added: list[_Line] = field(default_factory=list)


@dataclass
class BehaviorDelta:
    file_path: str
    symbol: str | None
    symbol_id: str | None
    category: str
    summary: str
    before: str | None
    after: str | None
    before_line: int | None
    after_line: int | None

    @property
    def label(self) -> str:
        return CATEGORY_LABEL.get(self.category, "Logic")


def extract_behavior_deltas(
    changes: list[FileChange],
    functions: list[Symbol],
    *,
    skip_path=None,
) -> list[BehaviorDelta]:
    """Before/after statement pairs for every changed function, base and head both from the patch."""
    by_file: dict[str, list[Symbol]] = {}
    for symbol in functions:
        if symbol.kind == "function":
            by_file.setdefault(symbol.file_path, []).append(symbol)

    deltas: list[BehaviorDelta] = []
    for change in changes:
        if change.patch is None or (skip_path and skip_path(change.path)):
            continue
        language = _language(change.path)
        head_functions = by_file.get(change.path, [])
        head_names = {symbol.name for symbol in head_functions}
        grouped: dict[str | None, list[_Block]] = {}
        owners: dict[str | None, Symbol | None] = {}
        removed_functions: dict[str, _Line] = {}
        for block in _blocks(change.patch):
            for line in block.removed:
                name = _header_name(line.text)
                if name and name not in head_names and name not in removed_functions:
                    removed_functions[name] = line
            owner = _owner(block, head_functions)
            key = owner.id if owner else None
            owners[key] = owner
            grouped.setdefault(key, []).append(block)

        for name, line in removed_functions.items():
            deltas.append(
                BehaviorDelta(
                    file_path=change.path,
                    symbol=name,
                    symbol_id=None,
                    category="removed_function",
                    summary=f"{name} is defined at the base commit and is not defined at the head commit.",
                    before=_cap(line.text),
                    after=None,
                    before_line=line.old,
                    after_line=None,
                )
            )

        for key, blocks in grouped.items():
            owner = owners.get(key)
            found = _deltas_for(blocks, owner, change.path, language, removed_functions)
            deltas.extend(found)

    return _limit(deltas)


def _limit(deltas: list[BehaviorDelta]) -> list[BehaviorDelta]:
    per_symbol: dict[tuple[str, str | None], list[BehaviorDelta]] = {}
    order: list[tuple[str, str | None]] = []
    for delta in deltas:
        key = (delta.file_path, delta.symbol)
        if key not in per_symbol:
            per_symbol[key] = []
            order.append(key)
        per_symbol[key].append(delta)
    kept: list[BehaviorDelta] = []
    for key in order[:MAX_SYMBOLS]:
        items = per_symbol[key]
        items.sort(key=lambda item: (CATEGORY_ORDER.get(item.category, 9), item.after_line or item.before_line or 0))
        kept.extend(items[:MAX_DELTAS_PER_SYMBOL])
    return kept


def _deltas_for(blocks, owner: Symbol | None, path: str, language: str, removed_functions) -> list[BehaviorDelta]:
    removed: list[_Line] = []
    added: list[_Line] = []
    for block in blocks:
        removed.extend(line for line in block.removed if _meaningful(line.text))
        added.extend(line for line in block.added if _meaningful(line.text))
    removed = [line for line in removed if _header_name(line.text) not in removed_functions]
    if not removed and not added:
        return []

    for line in removed + added:
        line.kind, line.key = _classify(line, owner)

    _drop_unchanged(removed, added)
    _inline_returns(removed)
    _inline_returns(added)

    name = owner.name if owner else None
    owner_id = owner.id if owner else None
    out: list[BehaviorDelta] = []

    def emit(category: str, before: _Line | None, after: _Line | None, summary: str) -> None:
        out.append(
            BehaviorDelta(
                file_path=path,
                symbol=name,
                symbol_id=owner_id,
                category=category,
                summary=summary,
                before=_cap(before.text) if before else None,
                after=_cap(after.text) if after else None,
                before_line=before.old if before else None,
                after_line=after.new if after else None,
            )
        )

    for category in ("signature", "error", "return", "condition", "value", "call", "logging", "logic"):
        olds = [line for line in removed if line.kind == category and not line.consumed]
        news = [line for line in added if line.kind == category and not line.consumed]
        for before, after in _pair(olds, news, keyed=category == "value"):
            emit(category, before, after, _summary(category, before, after, language, name))

    if owner is None:
        # Module-level code: keep only facts that change what callers can observe.
        out = [delta for delta in out if delta.category in {"value", "error", "condition", "call"}]
    return out


# --- patch reading ---------------------------------------------------------


def _blocks(patch: str) -> list[_Block]:
    blocks: list[_Block] = []
    current: _Block | None = None
    old_no = new_no = None
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            match = _HUNK.match(raw)
            current = None
            if not match:
                old_no = new_no = None
                continue
            old_no, new_no = int(match.group(1)), int(match.group(2))
            continue
        if old_no is None or new_no is None or raw.startswith(("+++", "---", "\\")):
            continue
        if raw.startswith("-"):
            if current is None:
                current = _Block(anchor=new_no)
                blocks.append(current)
            current.removed.append(_Line(old=old_no, new=None, text=raw[1:].strip()))
            old_no += 1
        elif raw.startswith("+"):
            if current is None:
                current = _Block(anchor=new_no)
                blocks.append(current)
            current.added.append(_Line(old=None, new=new_no, text=raw[1:].strip()))
            new_no += 1
        else:
            current = None
            old_no += 1
            new_no += 1
    return blocks


def _owner(block: _Block, functions: list[Symbol]) -> Symbol | None:
    lines = [line.new for line in block.added if line.new is not None]
    if not lines:
        lines = [block.anchor, block.anchor - 1]
    best: Symbol | None = None
    for symbol in functions:
        if any(symbol.start_line <= line <= symbol.end_line for line in lines):
            if best is None or (symbol.end_line - symbol.start_line) < (best.end_line - best.start_line):
                best = symbol
    return best


# --- classification --------------------------------------------------------


def _meaningful(text: str) -> bool:
    if not text:
        return False
    if _COMMENT.match(text) or _TRIVIAL.match(text) or _IMPORT.match(text):
        return False
    return True


def _header_name(text: str) -> str | None:
    match = _HEADER.match(text)
    if not match:
        return None
    for group in ("py", "js", "go", "arrow", "method"):
        name = match.group(group)
        if name and name not in _NOT_CALLS:
            return name
    return None


def _classify(line: _Line, owner: Symbol | None) -> tuple[str, str | None]:
    text = line.text
    header = _header_name(text)
    if header and (owner is None or header == owner.name):
        return "signature", header
    if owner is not None and line.new is not None and line.new == owner.start_line:
        return "signature", owner.name
    if _ERROR.search(text):
        return "error", None
    if _RETURN.match(text):
        return "return", None
    if _CONDITION.match(text):
        return "condition", None
    if _LOGGING.match(text):
        return "logging", None
    assign = _ASSIGN.match(text.rstrip(";,"))
    if assign and "(" not in assign.group("lhs"):
        return "value", assign.group("lhs")
    keyed = _KEYED.match(text.rstrip(";,"))
    if keyed:
        return "value", keyed.group("lhs")
    if call_names(text):
        return "call", None
    return "logic", None


def call_names(text: str) -> list[str]:
    names = []
    for match in _CALL_NAME.finditer(text):
        name = match.group(1)
        if _CONSTRUCTED.search(text[: match.start()]):
            continue
        if name.split(".")[-1] in _NOT_CALLS or name in _NOT_CALLS:
            continue
        if name not in names:
            names.append(name)
    return names


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).rstrip(";,")


def _drop_unchanged(removed: list[_Line], added: list[_Line]) -> None:
    """A line moved or re-indented without edits is not a behavior change."""
    remaining = {}
    for line in added:
        remaining.setdefault(_norm(line.text), []).append(line)
    for line in removed:
        twins = remaining.get(_norm(line.text))
        if twins:
            twin = twins.pop(0)
            line.consumed = True
            twin.consumed = True


def _inline_returns(lines: list[_Line]) -> None:
    """`x = <expr>; return x` reads as `return <expr>` so before and after compare like for like."""
    values = {line.key: line for line in lines if line.kind == "value" and line.key and not line.consumed}
    for line in lines:
        if line.kind != "return" or line.consumed:
            continue
        expr = re.sub(r"^(?:return|yield)\s+", "", line.text).rstrip(";").strip()
        if not _IDENT.match(expr):
            continue
        source = values.get(expr)
        if source is None:
            continue
        match = _ASSIGN.match(source.text.rstrip(";,"))
        if not match:
            continue
        rhs = match.group("rhs").rstrip(";").strip()
        line.text = f"return {rhs}"
        source.consumed = True


def _pair(olds: list[_Line], news: list[_Line], *, keyed: bool) -> list[tuple[_Line | None, _Line | None]]:
    pairs: list[tuple[_Line | None, _Line | None]] = []
    free_news = list(news)
    for before in olds:
        best = None
        best_score = 0.0
        for after in free_news:
            if keyed:
                if before.key != after.key:
                    continue
                score = 1.0
            else:
                score = difflib.SequenceMatcher(None, before.text, after.text).ratio()
                if before.kind in {"signature", "return", "error"}:
                    score += 0.5
            if score > best_score:
                best, best_score = after, score
        if best is not None and best_score >= 0.35:
            free_news.remove(best)
            pairs.append((before, best))
        else:
            pairs.append((before, None))
    pairs.extend((None, after) for after in free_news)
    pairs.sort(key=lambda pair: (pair[1].new if pair[1] else 10**9, pair[0].old if pair[0] else 10**9))
    return pairs


# --- summaries ---------------------------------------------------------------


def _summary(category: str, before: _Line | None, after: _Line | None, language: str, name: str | None) -> str:
    if category == "signature":
        return _signature_summary(before, after, language, name)
    if category == "error":
        if before and after:
            return f"Fails differently: {_error_target(before.text)} becomes {_error_target(after.text)}."
        if after:
            return f"Now fails with {_error_target(after.text)} on this path."
        return f"No longer fails with {_error_target(before.text)} on this path."
    if category == "return":
        if before and after:
            return "The returned expression changed."
        if after:
            return "Adds a return statement that the base commit did not have."
        return "Removes a return statement that the base commit had."
    if category == "condition":
        if before and after:
            return "The branch condition changed."
        if after:
            return "Adds a branch or guard that the base commit did not have."
        return "Removes a branch or guard that the base commit had."
    if category == "value":
        key = (after or before).key or "a value"
        if before and after:
            return f"{key} changes from {_rhs(before.text)} to {_rhs(after.text)}."
        if after:
            return f"Sets {key} to {_rhs(after.text)}, which was not set here before."
        return f"No longer sets {key} (was {_rhs(before.text)})."
    if category == "call":
        old_calls = call_names(before.text) if before else []
        new_calls = call_names(after.text) if after else []
        gained = [item for item in new_calls if item not in old_calls]
        lost = [item for item in old_calls if item not in new_calls]
        if before and after:
            if gained and lost:
                return f"Calls {_names(gained)} where it used to call {_names(lost)}."
            if gained:
                return f"Now also calls {_names(gained)}."
            if lost:
                return f"No longer calls {_names(lost)}."
            return "Passes different arguments to the same call."
        if after:
            return f"Now calls {_names(new_calls)}." if new_calls else "Adds a call."
        return f"No longer calls {_names(old_calls)}." if old_calls else "Removes a call."
    if category == "logging":
        if before and after:
            return "Log or console output changed."
        return "Adds log output." if after else "Removes log output."
    if before and after:
        return "Statement logic changed."
    return "Adds a statement." if after else "Removes a statement."


def _signature_summary(before: _Line | None, after: _Line | None, language: str, name: str | None) -> str:
    label = name or "The function"
    if before is None:
        return f"{label} gains a new definition."
    if after is None:
        return f"{label} lost its previous definition line."
    old = _params(before.text, language)
    new = _params(after.text, language)
    old_names = [item[0] for item in old]
    new_names = [item[0] for item in new]
    parts: list[str] = []
    gained = [item for item in new_names if item not in old_names]
    lost = [item for item in old_names if item not in new_names]
    if gained:
        parts.append(f"now takes {_names(gained)}")
    if lost:
        parts.append(f"no longer takes {_names(lost)}")
    old_defaults = dict(old)
    for param, default in new:
        if param in old_defaults and old_defaults[param] != default and (default or old_defaults[param]):
            parts.append(f"default of {param} changes from {old_defaults[param] or 'none'} to {default or 'none'}")
    if not parts:
        if _return_annotation(before.text) != _return_annotation(after.text):
            parts.append("declared return type changed")
        elif "async" in after.text.split() and "async" not in before.text.split():
            parts.append("is now async, so callers receive a promise or coroutine")
        elif "async" in before.text.split() and "async" not in after.text.split():
            parts.append("is no longer async")
        else:
            parts.append("parameter types or modifiers changed")
    sentence = "; ".join(parts)
    required = [param for param, default in new if param in gained and default is None]
    if lost or required:
        tail = " Every existing caller must match the new contract."
    elif any("default of" in part for part in parts):
        tail = " Callers that omit that argument now get the new default."
    else:
        tail = ""
    return f"{label} {sentence}.{tail}"


def _params(header: str, language: str) -> list[tuple[str, str | None]]:
    start = header.find("(")
    if start < 0:
        return []
    depth = 0
    end = len(header)
    for index in range(start, len(header)):
        if header[index] in "([{<":
            depth += 1
        elif header[index] in ")]}>":
            depth -= 1
            if depth == 0:
                end = index
                break
    inner = header[start + 1 : end]
    if language == "go" and header.startswith("func ("):
        # Skip the receiver: params start at the second parenthesised group.
        rest = header[end + 1 :]
        return _params(rest[rest.find("(") :], "other") if "(" in rest else []
    params: list[tuple[str, str | None]] = []
    for raw in _split_top(inner):
        piece = raw.strip()
        if not piece or piece in {"self", "cls", "*", "/"}:
            continue
        default = None
        if "=" in piece:
            piece, default = piece.split("=", 1)
            default = default.strip()
        tokens = re.findall(r"[A-Za-z_]\w*", piece.split(":")[0] if language in {"python", "typescript"} else piece)
        if not tokens:
            continue
        if language == "java":
            param = tokens[-1]
        else:
            param = tokens[0]
        if param in {"self", "cls"}:
            continue
        params.append((param, default))
    return params


def _split_top(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for char in text:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    return parts


def _return_annotation(header: str) -> str:
    close = header.rfind(")")
    return re.sub(r"\s+", "", header[close + 1 :]).rstrip("{:") if close >= 0 else ""


def _error_target(text: str) -> str:
    match = re.search(r"(?:raise|throw)\s+(?:new\s+)?([A-Za-z_][\w.]*)", text)
    if match:
        return match.group(1)
    match = re.search(r"(?:except|catch)\s*\(?\s*([A-Za-z_][\w.]*)", text)
    if match:
        return f"a handler for {match.group(1)}"
    match = re.search(r"(HTTPException|errors\.New|fmt\.Errorf|panic|abort)\s*\(([^)]{0,40})", text)
    if match:
        return f"{match.group(1)}({match.group(2).strip()})"
    return "an error"


def _rhs(text: str) -> str:
    match = _ASSIGN.match(text.rstrip(";,")) or _KEYED.match(text.rstrip(";,"))
    value = match.group("rhs").strip() if match else text
    return _cap(value, 60)


def _names(names: list[str]) -> str:
    shown = names[:3]
    text = ", ".join(shown)
    if len(names) > 3:
        text += f" and {len(names) - 3} more"
    return text


def _cap(text: str, limit: int = TEXT_CAP) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _language(path: str) -> str:
    ext = posixpath.splitext(path)[1].lower()
    if ext == ".py":
        return "python"
    if ext == ".go":
        return "go"
    if ext == ".java":
        return "java"
    if ext in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return "typescript"
    return "other"
