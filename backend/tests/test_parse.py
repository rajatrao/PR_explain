from pathlib import Path

from app.analyzer.parse import parse_file

_RUN_PAGE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "RunPage.tsx"


def test_many_exported_functions_keep_source_lines():
    """tree-sitter 0.26 corrupts node positions from the ABI 14 TypeScript grammar.

    A few hundred top-level functions is enough for start_point.row to become
    garbage and for the walk to segfault. 0.25.x returns the real lines.
    """
    count = 300
    source = "".join(
        f"export function f{i}(x: number) {{ return x + {i}; }}\n" for i in range(count)
    )
    symbols, imports, calls = parse_file("many.tsx", source)
    functions = [symbol for symbol in symbols if symbol.kind == "function"]
    assert [symbol.name for symbol in functions] == [f"f{i}" for i in range(count)]
    assert [symbol.start_line for symbol in functions] == list(range(1, count + 1))
    assert [symbol.end_line for symbol in functions] == list(range(1, count + 1))
    assert imports == []
    assert calls == []


def test_run_page_type_predicate_parses():
    source = _RUN_PAGE.read_text(encoding="utf-8")
    symbols, imports, calls = parse_file("frontend/src/RunPage.tsx", source)
    functions = {symbol.name: symbol for symbol in symbols if symbol.kind == "function"}
    predicate = functions["isDetailRow"]
    assert predicate.start_line >= 1
    assert predicate.end_line >= predicate.start_line
    assert predicate.end_line < 1000
    assert imports
    assert calls
