from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Language, Parser
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts

from app.analyzer.types import Symbol

_TS = Language(tsts.language_typescript())
_TSX = Language(tsts.language_tsx())
_JS = Language(tsjs.language())

_PARSERS = {
    ".ts": Parser(_TS),
    ".tsx": Parser(_TSX),
    ".js": Parser(_JS),
    ".jsx": Parser(_JS),
    ".mjs": Parser(_JS),
    ".cjs": Parser(_JS),
}

CODE_SUFFIXES = tuple(_PARSERS)


@dataclass
class ImportBinding:
    file_path: str
    local_name: str
    imported_name: str
    module: str
    line: int


@dataclass
class CallSite:
    file_path: str
    callee: str
    line: int
    column: int
    member: bool = False


def is_code_path(path: str) -> bool:
    return path.endswith(CODE_SUFFIXES)


def _line_span(node) -> tuple[int, int]:
    start = node.start_point.row + 1
    end = node.end_point.row + 1
    if node.end_point.column == 0 and end > start:
        end -= 1
    return start, end


def _text(source: bytes, node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _is_exported(node) -> bool:
    current = node
    while current is not None:
        if current.type == "export_statement":
            return True
        current = current.parent
    return False


def _function_name(node, source: bytes) -> str | None:
    for child in node.children:
        if child.type in {"identifier", "property_identifier"}:
            return _text(source, child)
    return None


def parse_file(path: str, text: str) -> tuple[list[Symbol], list[ImportBinding], list[CallSite]]:
    suffix = ""
    for candidate in CODE_SUFFIXES:
        if path.endswith(candidate):
            suffix = candidate
            break
    parser = _PARSERS.get(suffix)
    if parser is None:
        return [], [], []
    source = text.encode("utf-8")
    tree = parser.parse(source)
    symbols: list[Symbol] = []
    imports: list[ImportBinding] = []
    calls: list[CallSite] = []
    for node in _walk(tree.root_node):
        if node.type in {"function_declaration", "method_definition"}:
            name = _function_name(node, source)
            if not name:
                continue
            start, end = _line_span(node)
            symbols.append(
                Symbol(
                    id=_symbol_id(path, name, start),
                    name=name,
                    kind="function",
                    file_path=path,
                    start_line=start,
                    end_line=end,
                    exported=_is_exported(node),
                )
            )
        elif node.type == "variable_declarator":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            value = next(
                (
                    c
                    for c in node.children
                    if c.type in {"arrow_function", "function_expression"}
                ),
                None,
            )
            if name_node is None or value is None:
                continue
            name = _text(source, name_node)
            start, end = _line_span(node)
            symbols.append(
                Symbol(
                    id=_symbol_id(path, name, start),
                    name=name,
                    kind="function",
                    file_path=path,
                    start_line=start,
                    end_line=end,
                    exported=_is_exported(node),
                )
            )
        elif node.type == "import_statement":
            imports.extend(_imports_from(path, source, node))
        elif node.type == "call_expression":
            callee, member = _callee_name(source, node)
            if callee:
                calls.append(
                    CallSite(
                        file_path=path,
                        callee=callee,
                        line=node.start_point.row + 1,
                        column=node.start_point.column,
                        member=member,
                    )
                )
    return symbols, imports, calls


def _symbol_id(path: str, name: str, start: int) -> str:
    safe_path = path.replace("/", "_").replace(".", "_")
    return f"sym_{safe_path}_{name}_{start}"


def _callee_name(source: bytes, node) -> tuple[str | None, bool]:
    for child in node.children:
        if child.type == "identifier":
            return _text(source, child), False
        if child.type == "member_expression":
            prop = None
            for part in child.children:
                if part.type in {"property_identifier", "identifier"}:
                    prop = _text(source, part)
            return prop, True
        if child.type not in {"(", "arguments"}:
            break
    return None, False


def _imports_from(path: str, source: bytes, node) -> list[ImportBinding]:
    module = None
    for child in node.children:
        if child.type == "string":
            module = _text(source, child).strip().strip("'\"")
            break
    if not module:
        return []
    bindings: list[ImportBinding] = []
    for desc in _walk(node):
        if desc.type != "import_specifier":
            continue
        identifiers = [c for c in desc.children if c.type == "identifier"]
        if not identifiers:
            continue
        imported = _text(source, identifiers[0])
        local = _text(source, identifiers[-1])
        bindings.append(
            ImportBinding(
                file_path=path,
                local_name=local,
                imported_name=imported,
                module=module,
                line=desc.start_point.row + 1,
            )
        )
    for child in node.children:
        if child.type == "import_clause":
            for clause_child in child.children:
                if clause_child.type == "identifier":
                    name = _text(source, clause_child)
                    bindings.append(
                        ImportBinding(
                            file_path=path,
                            local_name=name,
                            imported_name="default",
                            module=module,
                            line=clause_child.start_point.row + 1,
                        )
                    )
    return bindings
