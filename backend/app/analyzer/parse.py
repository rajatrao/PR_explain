from __future__ import annotations

import posixpath
from dataclasses import dataclass

from tree_sitter import Language, Parser
import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjs
import tree_sitter_python as tspy
import tree_sitter_typescript as tsts

from app.analyzer.types import Symbol

_TS = Language(tsts.language_typescript())
_TSX = Language(tsts.language_tsx())
_JS = Language(tsjs.language())
_PY = Language(tspy.language())
_GO = Language(tsgo.language())
_JAVA = Language(tsjava.language())

_PARSERS = {
    ".ts": Parser(_TS),
    ".tsx": Parser(_TSX),
    ".js": Parser(_JS),
    ".jsx": Parser(_JS),
    ".mjs": Parser(_JS),
    ".cjs": Parser(_JS),
    ".py": Parser(_PY),
    ".go": Parser(_GO),
    ".java": Parser(_JAVA),
}

# JavaScript stays with TypeScript: the coverage chip already says TypeScript for those files.
_LANGUAGES = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".mjs": "typescript",
    ".cjs": "typescript",
    ".py": "python",
    ".go": "go",
    ".java": "java",
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


def is_package_marker(path: str) -> bool:
    """Package markers carry no reviewable symbols. TypeScript index.ts is not one."""
    return posixpath.basename(path) == "__init__.py"


def is_dunder_name(name: str | None) -> bool:
    """Special methods such as __init__ and __repr__. A leading underscore is not enough."""
    if not name or len(name) < 5:
        return False
    return name.startswith("__") and name.endswith("__")


def is_private_python_name(name: str | None, file_path: str | None) -> bool:
    """Leading-underscore Python functions and methods. Other languages are unchanged."""
    if not name or name.startswith("__"):
        return False
    if not name.startswith("_"):
        return False
    return language_of(file_path or "") == "python"


def is_test_path(path: str) -> bool:
    base = posixpath.basename(path)
    if ".test." in base or ".spec." in base:
        return True
    if base.endswith("_test.go"):
        return True
    if base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py")):
        return True
    if base.endswith("Test.java") or base.endswith("Tests.java"):
        return True
    return False


def is_code_path(path: str) -> bool:
    return path.endswith(CODE_SUFFIXES) and not is_package_marker(path)


def language_of(path: str) -> str | None:
    for suffix, language in _LANGUAGES.items():
        if path.endswith(suffix):
            return language
    return None


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
    if is_package_marker(path):
        return [], [], []
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
    if suffix == ".py":
        return _parse_python(path, source, tree)
    if suffix == ".go":
        return _parse_go(path, source, tree)
    if suffix == ".java":
        return _parse_java(path, source, tree)
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


def _make_symbol(path: str, name: str, node, *, exported: bool) -> Symbol:
    start, end = _line_span(node)
    return Symbol(
        id=_symbol_id(path, name, start),
        name=name,
        kind="function",
        file_path=path,
        start_line=start,
        end_line=end,
        exported=exported,
    )


def _make_call(path: str, node, callee: str | None, member: bool) -> CallSite | None:
    if not callee:
        return None
    return CallSite(
        file_path=path,
        callee=callee,
        line=node.start_point.row + 1,
        column=node.start_point.column,
        member=member,
    )


def _binding(path: str, local_name: str, imported_name: str, module: str, node) -> ImportBinding:
    return ImportBinding(
        file_path=path,
        local_name=local_name,
        imported_name=imported_name,
        module=module,
        line=node.start_point.row + 1,
    )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
        return value[1:-1]
    return value


def _last_identifier(source: bytes, node) -> str:
    if node.type == "identifier":
        return _text(source, node)
    found = ""
    for child in node.children:
        if child.type == "identifier":
            found = _text(source, child)
    return found or _text(source, node)


def _parse_python(path: str, source: bytes, tree) -> tuple[list[Symbol], list[ImportBinding], list[CallSite]]:
    symbols: list[Symbol] = []
    imports: list[ImportBinding] = []
    calls: list[CallSite] = []
    for node in _walk(tree.root_node):
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            symbols.append(
                _make_symbol(path, _text(source, name_node), node, exported=_python_exported(node))
            )
        elif node.type in {"import_statement", "import_from_statement"}:
            imports.extend(_python_imports(path, source, node))
        elif node.type == "call":
            site = _make_call(path, node, *_python_callee(source, node))
            if site:
                calls.append(site)
    return symbols, imports, calls


def _python_exported(node) -> bool:
    parent = node.parent
    if parent is not None and parent.type == "decorated_definition":
        parent = parent.parent
    return parent is not None and parent.type == "module"


def _python_callee(source: bytes, node) -> tuple[str | None, bool]:
    func = node.child_by_field_name("function")
    if func is None:
        return None, False
    if func.type == "identifier":
        return _text(source, func), False
    if func.type == "attribute":
        attr = func.child_by_field_name("attribute")
        if attr is None:
            return None, True
        return _text(source, attr), True
    return None, False


def _python_imports(path: str, source: bytes, node) -> list[ImportBinding]:
    if node.type == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            return []
        module = _text(source, module_node)
        bindings: list[ImportBinding] = []
        for child in node.children:
            if child.type == "dotted_name":
                imported = _last_identifier(source, child)
                if imported:
                    bindings.append(_binding(path, imported, imported, module, child))
            elif child.type == "aliased_import":
                imported, local = _alias_parts(source, child)
                if imported and local:
                    bindings.append(_binding(path, local, imported, module, child))
        return bindings
    bindings = []
    for child in node.children:
        if child.type == "dotted_name":
            module = _text(source, child)
            local = module.split(".")[-1]
            bindings.append(_binding(path, local, local, module, child))
        elif child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if name_node is None or alias_node is None:
                continue
            module = _text(source, name_node)
            local = _text(source, alias_node)
            imported = module.split(".")[-1]
            bindings.append(_binding(path, local, imported, module, child))
    return bindings


def _alias_parts(source: bytes, node) -> tuple[str | None, str | None]:
    name_node = node.child_by_field_name("name")
    alias_node = node.child_by_field_name("alias")
    if name_node is None:
        return None, None
    if name_node.type == "dotted_name":
        imported = _last_identifier(source, name_node)
    else:
        imported = _text(source, name_node)
    local = _text(source, alias_node) if alias_node is not None else imported
    return imported, local


def _parse_go(path: str, source: bytes, tree) -> tuple[list[Symbol], list[ImportBinding], list[CallSite]]:
    symbols: list[Symbol] = []
    imports: list[ImportBinding] = []
    calls: list[CallSite] = []
    for node in _walk(tree.root_node):
        if node.type in {"function_declaration", "method_declaration"}:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(source, name_node)
            symbols.append(_make_symbol(path, name, node, exported=_go_exported(name)))
        elif node.type == "import_declaration":
            imports.extend(_go_imports(path, source, node))
        elif node.type == "call_expression":
            site = _make_call(path, node, *_go_callee(source, node))
            if site:
                calls.append(site)
    return symbols, imports, calls


def _go_exported(name: str) -> bool:
    return name[:1].isupper()


def _go_callee(source: bytes, node) -> tuple[str | None, bool]:
    func = node.child_by_field_name("function")
    if func is None:
        return None, False
    if func.type == "identifier":
        return _text(source, func), False
    if func.type == "selector_expression":
        field = func.child_by_field_name("field")
        if field is None:
            return None, True
        return _text(source, field), True
    return None, False


def _go_imports(path: str, source: bytes, node) -> list[ImportBinding]:
    bindings: list[ImportBinding] = []
    for spec in _walk(node):
        if spec.type != "import_spec":
            continue
        path_node = spec.child_by_field_name("path")
        if path_node is None:
            continue
        module = _unquote(_text(source, path_node))
        last = module.rstrip("/").split("/")[-1] if module else module
        name_node = spec.child_by_field_name("name")
        local = _text(source, name_node) if name_node is not None else last
        bindings.append(_binding(path, local, last, module, spec))
    return bindings


def _parse_java(path: str, source: bytes, tree) -> tuple[list[Symbol], list[ImportBinding], list[CallSite]]:
    symbols: list[Symbol] = []
    imports: list[ImportBinding] = []
    calls: list[CallSite] = []
    for node in _walk(tree.root_node):
        if node.type in {"method_declaration", "constructor_declaration"}:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            symbols.append(
                _make_symbol(path, _text(source, name_node), node, exported=_java_public(node))
            )
        elif node.type == "import_declaration":
            imports.extend(_java_imports(path, source, node))
        elif node.type == "method_invocation":
            site = _make_call(path, node, *_java_callee(source, node))
            if site:
                calls.append(site)
    return symbols, imports, calls


def _java_public(node) -> bool:
    for child in node.children:
        if child.type != "modifiers":
            continue
        for modifier in child.children:
            if modifier.type == "public":
                return True
    return False


def _java_callee(source: bytes, node) -> tuple[str | None, bool]:
    name = node.child_by_field_name("name")
    if name is None:
        return None, False
    return _text(source, name), node.child_by_field_name("object") is not None


def _java_imports(path: str, source: bytes, node) -> list[ImportBinding]:
    static = any(child.type == "static" for child in node.children)
    target = next(
        (child for child in node.children if child.type in {"scoped_identifier", "identifier"}),
        None,
    )
    if target is None:
        return []
    if target.type == "identifier":
        name = _text(source, target)
        return [_binding(path, name, name, name, target)]
    simple = target.child_by_field_name("name")
    scope = target.child_by_field_name("scope")
    imported = _text(source, simple) if simple is not None else _text(source, target)
    if static and scope is not None:
        module = _text(source, scope)
    else:
        module = _text(source, target)
    return [_binding(path, imported, imported, module, target)]
