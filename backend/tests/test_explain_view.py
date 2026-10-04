from app.analyzer.analyze import analyze
from app.analyzer.diagram import build_change_flow
from app.analyzer.parse import is_private_python_name
from app.analyzer.types import FileChange, Snapshot
from app.explanation.explain_view import without_hidden_symbols
from app.explanation.narrate import explain_bullets


def test_private_python_name_is_scoped_to_python_files():
    assert is_private_python_name("_configured", "backend/app/llm/provider.py")
    assert not is_private_python_name("_configured", "frontend/src/app.ts")
    assert not is_private_python_name("__init__", "backend/app/pkg/__init__.py")


def test_explain_bullets_skip_tests_and_private_python():
    result = analyze(
        Snapshot(
            repository="fixture/private",
            base_sha="a" * 40,
            head_sha="b" * 40,
            files={
                "backend/app/llm/provider.py": (
                    "def explain():\n    return 1\n\n"
                    "def _configured():\n    return explain()\n"
                ),
                "backend/tests/test_provider.py": (
                    "from app.llm.provider import explain\n\n"
                    "def test_explain():\n    return explain()\n"
                ),
            },
            changes=[
                FileChange(path="backend/app/llm/provider.py", status="modified", patch=None),
                FileChange(path="backend/tests/test_provider.py", status="modified", patch=None),
            ],
        )
    )
    bullets = explain_bullets(result.claims, result.symbols)
    joined = "\n".join(bullets)
    assert "explain changed" in joined
    assert "_configured" not in joined
    assert "test_provider.py" not in joined
    assert "No test references" not in joined

    explain_flow = build_change_flow(result.symbols, result.relationships, result.evidences, for_explain=True)
    assert "_configured" not in explain_flow["mermaid"]
    assert "explain" in explain_flow["mermaid"]
    assert "test_provider.py" not in explain_flow["text"]
    assert "Tests" not in explain_flow["text"]

    class _Claim:
        def __init__(self, kind, subject, text):
            self.kind = kind
            self.subject = subject
            self.text = text

    class _Symbol:
        def __init__(self, name, file_path):
            self.kind = "function"
            self.name = name
            self.file_path = file_path

    reach = (
        "backend/app/worker.py is absent from the diff, but a call or import path reaches "
        "changed symbol _configured, _openai_provider, create_llm_provider, so its behavior is not unchanged."
    )
    rewritten = explain_bullets(
        [_Claim("reaches_changed", "backend/app/worker.py", reach)],
        [
            _Symbol("_configured", "backend/app/llm/provider.py"),
            _Symbol("_openai_provider", "backend/app/llm/provider.py"),
            _Symbol("create_llm_provider", "backend/app/llm/provider.py"),
        ],
    )
    reach_lines = [line for line in rewritten if "worker.py" in line]
    assert reach_lines
    assert "_configured" not in reach_lines[0]
    assert "_openai_provider" not in reach_lines[0]
    assert "create_llm_provider" in reach_lines[0]
    assert without_hidden_symbols(reach, {"_configured", "_openai_provider"}) == (
        "backend/app/worker.py is absent from the diff, but a call or import path reaches "
        "changed symbol create_llm_provider, so its behavior is not unchanged."
    )
