from pathlib import Path

from app.analyzer.parse import is_package_marker, is_test_path, parse_file

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


def test_python_function_call_is_not_an_import():
    source = "from .other import helper\nimport os\n\ndef run():\n    return helper()\n"
    symbols, imports, calls = parse_file("pkg/app.py", source)
    functions = {symbol.name: symbol for symbol in symbols}
    assert functions["run"].kind == "function"
    assert functions["run"].exported is True
    assert functions["run"].start_line == 4
    assert [(call.callee, call.member) for call in calls] == [("helper", False)]
    assert {binding.imported_name for binding in imports} == {"helper", "os"}
    assert ("helper", ".other") in {(binding.imported_name, binding.module) for binding in imports}
    assert all(call.callee not in {"os", "other"} for call in calls)


def test_go_function_call_is_not_an_import():
    source = (
        'package main\n\nimport "fmt"\n\n'
        "func helper() int { return 1 }\n\n"
        "func Run() int {\n    fmt.Println(helper())\n    return helper()\n}\n"
    )
    symbols, imports, calls = parse_file("main.go", source)
    functions = {symbol.name: symbol for symbol in symbols}
    assert functions["helper"].exported is False
    assert functions["Run"].exported is True
    bare = [call for call in calls if call.callee == "helper" and not call.member]
    assert len(bare) == 2
    assert any(call.callee == "Println" and call.member for call in calls)
    assert [(binding.module, binding.local_name) for binding in imports] == [("fmt", "fmt")]
    assert all(call.callee != "fmt" for call in calls)


def test_java_method_call_is_not_an_import():
    source = (
        "import com.example.Util;\n"
        "import static com.example.Util.helper;\n\n"
        "class App {\n"
        "    public int helper() { return 1; }\n\n"
        "    int run() {\n"
        "        return helper();\n"
        "    }\n"
        "}\n"
    )
    symbols, imports, calls = parse_file("App.java", source)
    functions = {symbol.name: symbol for symbol in symbols}
    assert functions["helper"].kind == "function"
    assert functions["helper"].exported is True
    assert functions["run"].exported is False
    assert [(call.callee, call.member) for call in calls] == [("helper", False)]
    assert ("helper", "com.example.Util") in {
        (binding.imported_name, binding.module) for binding in imports
    }
    assert ("Util", "com.example.Util") in {
        (binding.imported_name, binding.module) for binding in imports
    }
    assert all(call.callee not in {"Util", "com"} for call in calls)


def test_init_py_emits_no_symbol_and_production_modules_still_parse():
    symbols, imports, calls = parse_file(
        "pkg/__init__.py",
        "def helper():\n    return helper()\n",
    )
    assert symbols == []
    assert imports == []
    assert calls == []
    provider, _, _ = parse_file("pkg/provider.py", "def helper():\n    return 1\n")
    assert [symbol.name for symbol in provider] == ["helper"]
    assert is_package_marker("pkg/__init__.py")
    assert not is_package_marker("pkg/provider.py")
    assert not is_package_marker("frontend/src/index.ts")
    for path in (
        "pkg/test_provider.py",
        "pkg/provider_test.py",
        "widget_test.go",
        "AppTest.java",
        "AppTests.java",
        "src/login.test.ts",
        "src/login.spec.tsx",
    ):
        assert is_test_path(path)
    for path in ("provider.py", "config.py", "app.py", "main.py", "src/index.ts", "src/widget.ts"):
        assert not is_test_path(path)


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
