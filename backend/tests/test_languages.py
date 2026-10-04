from app.analyzer.analyze import analyze
from app.analyzer.diagram import build_change_flow
from app.analyzer.types import FileChange, Snapshot
from app.explanation.details import build_details


def _snapshot(files: dict[str, str], changed: list[str]) -> Snapshot:
    return Snapshot(
        repository="fixture/polyglot",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=files,
        changes=[FileChange(path=path, status="modified", patch=None) for path in changed],
    )


def _labels(result, title: str) -> list[str]:
    flow = build_change_flow(result.symbols, result.relationships, result.evidences)
    details = build_details(
        symbols=result.symbols,
        relationships=result.relationships,
        evidences=result.evidences,
        claims=result.claims,
        sections=flow["sections"],
        repo="fixture/polyglot",
        sha="b" * 40,
    )
    section = next(item for item in details["sections"] if item["title"] == title)
    return [row["label"] for row in section["rows"]]


def test_test_files_are_not_diagram_nodes_but_still_count_as_tests():
    result = analyze(
        _snapshot(
            {
                "pkg/provider.py": "def helper():\n    return 1\n",
                "pkg/config.py": "def setting():\n    return helper()\n",
                "pkg/__init__.py": "def hidden():\n    return 1\n",
                "pkg/test_provider.py": "from .provider import helper\n\ndef test_helper():\n    return helper()\n",
            },
            ["pkg/provider.py", "pkg/__init__.py", "pkg/test_provider.py"],
        )
    )
    assert not any(symbol.file_path.endswith("__init__.py") for symbol in result.symbols)
    assert not any(symbol.file_path.endswith("test_provider.py") for symbol in result.symbols)
    assert any(claim.kind == "symbol_changed" and claim.subject == "helper" for claim in result.claims)
    assert not any(claim.kind == "symbol_changed" and claim.subject in {"test_helper", "hidden"} for claim in result.claims)
    assert any(claim.kind == "tests" and claim.subject == "helper" for claim in result.claims)
    assert not any(claim.kind == "missing_test" and claim.subject == "helper" for claim in result.claims)
    assert any(claim.kind == "file_changed" and claim.subject == "pkg/__init__.py" for claim in result.claims)
    assert any(claim.kind == "file_changed" and claim.subject == "pkg/test_provider.py" for claim in result.claims)
    flow = build_change_flow(result.symbols, result.relationships, result.evidences)
    assert "helper" in flow["mermaid"]
    assert "test_helper" not in flow["mermaid"]
    assert "hidden" not in flow["mermaid"]
    assert "__init__.py" not in flow["mermaid"]
    assert "test_helper" not in _labels(result, "Key Changes")
    assert "pkg/test_provider.py" not in _labels(result, "Key Changes")
    assert "test_helper" not in _labels(result, "Reviewer Attention")
    assert "pkg/test_provider.py" not in _labels(result, "Reviewer Attention")
    assert any(symbol.name == "setting" and symbol.file_path == "pkg/config.py" for symbol in result.symbols)


def test_dunder_methods_are_not_diagram_nodes_and_explain_still_is():
    result = analyze(
        _snapshot(
            {
                "backend/app/llm/openai_compat.py": (
                    "class OpenAICompatibleProvider:\n"
                    "    def __init__(self):\n"
                    "        self.value = explain()\n"
                    "\n"
                    "    def __repr__(self):\n"
                    "        return 'provider'\n"
                    "\n"
                    "def explain():\n"
                    "    return 1\n"
                    "\n"
                    "def _configured():\n"
                    "    return explain()\n"
                ),
            },
            ["backend/app/llm/openai_compat.py"],
        )
    )
    assert any(claim.kind == "symbol_changed" and claim.subject == "explain" for claim in result.claims)
    assert any(claim.kind == "symbol_changed" and claim.subject == "_configured" for claim in result.claims)
    assert not any(
        claim.kind == "symbol_changed" and claim.subject in {"__init__", "__repr__"} for claim in result.claims
    )
    assert not any(claim.kind == "missing_test" and claim.subject in {"__init__", "__repr__"} for claim in result.claims)
    flow = build_change_flow(result.symbols, result.relationships, result.evidences)
    explain_flow = build_change_flow(result.symbols, result.relationships, result.evidences, for_explain=True)
    assert "explain" in flow["mermaid"]
    assert "_configured" in flow["mermaid"]
    assert "explain" in explain_flow["mermaid"]
    assert "_configured" not in explain_flow["mermaid"]
    assert "__init__" not in flow["mermaid"]
    assert "__repr__" not in flow["mermaid"]
    assert "explain" in flow["text"]
    assert "__init__" not in flow["text"]
    assert "__repr__" not in flow["text"]
    assert "Tests" in flow["text"]
    assert "Tests" not in explain_flow["text"]
    assert "explain" in _labels(result, "Key Changes")
    assert "__init__" not in _labels(result, "Key Changes")
    assert "__repr__" not in _labels(result, "Key Changes")
    assert "__init__" not in _labels(result, "Reviewer Attention")
    assert "__repr__" not in _labels(result, "Reviewer Attention")


def test_only_a_test_file_does_not_fill_the_diagram():
    result = analyze(
        _snapshot(
            {
                "pkg/provider.py": "def helper():\n    return 1\n",
                "pkg/test_provider.py": "def test_helper():\n    return helper()\n\ndef test_extra():\n    return 1\n",
            },
            ["pkg/test_provider.py"],
        )
    )
    flow = build_change_flow(result.symbols, result.relationships, result.evidences)
    assert flow["mermaid"] == ""
    assert not any(claim.kind == "symbol_changed" for claim in result.claims)
    assert any(rel.type == "TESTS" and rel.target_name == "helper" for rel in result.relationships)


def test_python_call_changed_symbol_missing_test_and_diagram():
    source = "def helper():\n    return 1\n\ndef run():\n    return helper()\n"
    result = analyze(_snapshot({"app.py": source}, ["app.py"]))
    assert result.language_coverage == "Python"
    calls = [
        rel
        for rel in result.relationships
        if rel.type == "CALLS" and rel.source_name == "run" and rel.target_name == "helper"
    ]
    assert calls
    assert any(claim.kind == "symbol_changed" and claim.subject == "run" for claim in result.claims)
    assert any(claim.kind == "missing_test" and claim.subject == "helper" for claim in result.claims)
    assert not any(claim.kind == "diff_only" for claim in result.claims)
    flow = build_change_flow(result.symbols, result.relationships, result.evidences)
    assert "calls helper" in flow["text"]
    assert "helper" in flow["mermaid"]
    assert flow["mermaid"]


def test_python_test_call_clears_missing_test_and_import_is_not_a_call():
    result = analyze(
        _snapshot(
            {
                "pkg/app.py": "def helper():\n    return 1\n",
                "pkg/test_app.py": "from .app import helper\n\ndef test_helper():\n    return helper()\n",
            },
            ["pkg/app.py"],
        )
    )
    assert any(
        rel.type == "TESTS" and rel.target_name == "helper" and rel.source_file == "pkg/test_app.py"
        for rel in result.relationships
    )
    assert any(
        rel.type == "IMPORTS" and rel.target_name == "helper" and rel.source_file == "pkg/test_app.py"
        for rel in result.relationships
    )
    assert not any(claim.kind == "missing_test" and claim.subject == "helper" for claim in result.claims)
    assert not any(rel.type == "CALLS" and rel.target_name in {"app", "helper"} and rel.source_name == "pkg/test_app.py" for rel in result.relationships)


def test_go_and_java_calls_and_mixed_coverage():
    go_source = "package main\n\nfunc goHelper() int { return 1 }\n\nfunc goRun() int { return goHelper() }\n"
    java_source = (
        "class App {\n"
        "    int javaHelper() { return 1; }\n"
        "    int javaRun() { return javaHelper(); }\n"
        "}\n"
    )
    result = analyze(
        _snapshot(
            {
                "main.go": go_source,
                "App.java": java_source,
                "app.py": "def py_helper():\n    return 1\n\ndef py_run():\n    return py_helper()\n",
            },
            ["main.go", "App.java", "app.py"],
        )
    )
    assert result.language_coverage == "Python, Go, Java"
    assert len(result.language_coverage) <= 32
    assert any(
        rel.type == "CALLS"
        and rel.source_file == "main.go"
        and rel.source_name == "goRun"
        and rel.target_name == "goHelper"
        for rel in result.relationships
    )
    assert any(
        rel.type == "CALLS"
        and rel.source_file == "App.java"
        and rel.source_name == "javaRun"
        and rel.target_name == "javaHelper"
        for rel in result.relationships
    )
    flow = build_change_flow(result.symbols, result.relationships, result.evidences)
    assert flow["mermaid"]
    assert "calls goHelper" in flow["text"]
    assert "calls javaHelper" in flow["text"]


def test_python_outside_typescript_projects_is_drawn_without_call_edges():
    snapshot = _snapshot(
        {
            "frontend/tsconfig.json": "{}\n",
            "fixtures/oauth-ts/head/tsconfig.json": "{}\n",
            "frontend/src/app.ts": "export function ui(): number { return 1 }\n",
            "backend/app/config.py": (
                "class Settings:\n"
                "    def load(self):\n"
                "        return self.read()\n"
                "\n"
                "    def read(self):\n"
                "        return 1\n"
            ),
            "backend/app/other.py": "def untouched():\n    return 1\n",
        },
        ["backend/app/config.py"],
    )
    result = analyze(snapshot)
    assert result.language_coverage == "Python"
    functions = {
        symbol.name: symbol
        for symbol in result.symbols
        if symbol.kind == "function" and symbol.file_path == "backend/app/config.py"
    }
    assert functions["load"].changed
    assert functions["read"].changed
    assert any(symbol.name == "untouched" and symbol.file_path == "backend/app/other.py" for symbol in result.symbols)
    assert not any(symbol.name == "ui" for symbol in result.symbols)
    assert not any(rel.type == "CALLS" and rel.target_name == "read" for rel in result.relationships)
    assert not any(claim.kind == "diff_only" for claim in result.claims)
    assert not any("outside the TypeScript" in claim.text and "config.py" in claim.text for claim in result.claims)
    flow = build_change_flow(result.symbols, result.relationships, result.evidences)
    assert "flowchart TD" in flow["mermaid"]
    assert "load" in flow["mermaid"]
    assert "read" in flow["mermaid"]
    assert "-->|calls|" not in flow["mermaid"]
    assert "load" in flow["text"]


def test_all_parsed_languages_fit_the_coverage_column():
    result = analyze(
        _snapshot(
            {
                "app.ts": "export function helper(): number { return 1 }\n",
                "app.py": "def helper():\n    return 1\n",
                "main.go": "package main\n\nfunc Helper() int { return 1 }\n",
                "App.java": "class App {\n    public int helper() { return 1; }\n}\n",
            },
            ["app.ts"],
        )
    )
    assert result.language_coverage == "TypeScript, Python, Go, Java"
    assert len(result.language_coverage) <= 32
