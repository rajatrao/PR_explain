from app.analyzer.analyze import analyze
from app.analyzer.diagram import build_change_flow
from app.analyzer.fixture import load_oauth_snapshot
from app.analyzer.types import FileChange, Snapshot
from app.explanation.schema import ExplanationDocument
from app.github.comment import render_pull_request_comment


def test_change_flow_keeps_direct_calls_and_tests_only():
    result = analyze(load_oauth_snapshot())
    story = build_change_flow(result.symbols, result.relationships, result.evidences)
    text = story["text"]
    assert "Changed" in text
    assert "createSession" in text
    assert "no direct call found" in text
    assert "login calls createSession" in text
    assert "googleCallback calls createSession" in text
    assert "refreshToken calls createSession" in text
    assert "Reached from outside this diff" in text
    assert "none found for these symbols" in text
    assert "passwordLogin" not in text
    assert "health" not in text
    assert "login.test.ts" not in text
    assert "oauth.test.ts" not in text
    assert "IMPORTS" not in text
    assert "one-hop" not in text.lower()


def test_ambiguous_call_does_not_add_an_edge():
    snapshot = Snapshot(
        repository="fixture/ambiguous",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files={
            "src/a.ts": "export function run(): string { return 'a'; }\n",
            "src/b.ts": "export function run(): string { return 'b'; }\n",
            "src/c.ts": "export function start(): string { return run(); }\n",
            "tsconfig.json": "{}\n",
        },
        changes=[
            FileChange(
                path="src/c.ts",
                status="modified",
                patch="@@ -1 +1 @@\n-export function start(): string { return 'x'; }\n+export function start(): string { return run(); }\n",
            )
        ],
    )
    result = analyze(snapshot)
    story = build_change_flow(result.symbols, result.relationships, result.evidences)
    text = story["text"]
    assert "Changed" in text
    assert "start" in text
    assert "no direct call found" in text
    assert "none found for these symbols" in text
    assert "calls " not in text
    assert "Reached from outside this diff" not in text


def test_two_calls_and_one_outside_caller_render_story_headings():
    symbols = [
        _symbol("sym_widget", "Widget", "src/widget.ts", 1, changed=True),
        _symbol("sym_alpha", "alpha", "src/widget.ts", 20, changed=False),
        _symbol("sym_beta", "beta", "src/widget.ts", 30, changed=False),
        _symbol("sym_other", "other.ts", "src/other.ts", 1, changed=False),
    ]
    relationships = [
        _calls("sym_widget", "Widget", "src/widget.ts", "sym_alpha", "alpha", "src/widget.ts", "ev_alpha", 3),
        _calls("sym_widget", "Widget", "src/widget.ts", "sym_beta", "beta", "src/widget.ts", "ev_beta", 9),
        _calls("sym_other", "src/other.ts", "src/other.ts", "sym_widget", "Widget", "src/widget.ts", "ev_out", 4),
        _calls("sym_alpha", "alpha", "src/widget.ts", "sym_beta", "beta", "src/widget.ts", "ev_hop", 21),
        _rel("IMPORTS", "sym_other", "src/other.ts", "src/other.ts", "sym_widget", "Widget", "src/widget.ts", None),
        _rel("TESTS", "sym_test", "src/other.test.ts", "src/other.test.ts", "sym_alpha", "alpha", "src/widget.ts", "ev_test"),
    ]
    evidences = [
        _evidence("ev_alpha", "src/widget.ts", 3),
        _evidence("ev_beta", "src/widget.ts", 9),
        _evidence("ev_out", "src/other.ts", 4),
        _evidence("ev_hop", "src/widget.ts", 21),
        _evidence("ev_test", "src/other.test.ts", 2),
    ]
    story = build_change_flow(symbols, relationships, evidences)
    text = story["text"]
    headings = ("Changed", "Widget", "Reached from outside this diff", "Tests")
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "calls alpha" in text
    assert "calls beta" in text
    assert "other.ts calls Widget" in text
    assert "src/widget.ts:3" in text
    assert "src/widget.ts:9" in text
    assert "src/other.ts:4" in text
    assert "none found for these symbols" in text
    assert "one-hop" not in text
    assert "second hop" not in text.lower()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("calls "):
            assert ":" not in stripped
    explain_story = build_change_flow(symbols, relationships, evidences, for_explain=True)
    body = render_pull_request_comment(
        document=ExplanationDocument(summary="Widget changed."),
        failure=None,
        repo_full_name="acme/app",
        pr_number=1,
        head_sha="b" * 40,
        app_base_url="http://localhost:5173",
        run_id="run",
        evidence_by_id={},
        change_flow=explain_story["text"],
    )
    assert "Widget changed." not in body  # no summary section in Explain
    assert "### Change flow" not in body
    assert "- Changed" not in body
    assert "Reached from outside this diff" not in body
    assert "```\nChanged" not in body
    assert "one-hop" not in body.lower()


def test_explain_comment_mermaid_names_changed_symbols_without_invented_edges():
    symbols = [
        _symbol("sym_widget", "Widget", "src/widget.ts", 1, changed=True),
        _symbol("sym_alpha", "alpha", "src/widget.ts", 20, changed=False),
        _symbol("sym_beta", "beta", "src/widget.ts", 30, changed=False),
        _symbol("sym_other", "other.ts", "src/other.ts", 1, changed=False),
    ]
    relationships = [
        _calls("sym_widget", "Widget", "src/widget.ts", "sym_alpha", "alpha", "src/widget.ts", "ev_alpha", 3),
        _calls("sym_widget", "Widget", "src/widget.ts", "sym_beta", "beta", "src/widget.ts", "ev_beta", 9),
        _calls("sym_other", "src/other.ts", "src/other.ts", "sym_widget", "Widget", "src/widget.ts", "ev_out", 4),
        _calls("sym_alpha", "alpha", "src/widget.ts", "sym_beta", "beta", "src/widget.ts", "ev_hop", 21),
        _rel("IMPORTS", "sym_other", "src/other.ts", "src/other.ts", "sym_widget", "Widget", "src/widget.ts", None),
        _rel("TESTS", "sym_test", "src/other.test.ts", "src/other.test.ts", "sym_alpha", "alpha", "src/widget.ts", "ev_test"),
    ]
    story = build_change_flow(symbols, relationships, [], for_explain=True)
    body = render_pull_request_comment(
        document=ExplanationDocument(summary="Widget changed."),
        failure=None,
        repo_full_name="acme/app",
        pr_number=1,
        head_sha="b" * 40,
        app_base_url="http://localhost:5173",
        run_id="run",
        evidence_by_id={},
        change_flow=story["text"],
        bullets=["Widget changed."],
        mermaid=story["mermaid"],
    )
    assert "```mermaid" in body
    assert "### Change flow" not in body
    assert "- Changed" not in body
    assert "- Widget changed." not in body
    fence = body.split("```mermaid", 1)[1].split("```", 1)[0]
    assert "Widget" in fence
    assert "-->|calls|" in fence
    assert "-->|imports|" in fence
    assert fence.count("-->|calls|") == 3
    assert fence.count("-->|imports|") == 1
    assert "one-hop" not in body.lower()
    assert "TESTS" not in fence


class _Symbol:
    def __init__(self, symbol_id, name, file_path, start_line, changed):
        self.id = symbol_id
        self.name = name
        self.kind = "function"
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = start_line
        self.changed = changed


def _symbol(symbol_id, name, file_path, start_line, *, changed):
    return _Symbol(symbol_id, name, file_path, start_line, changed)


class _Rel:
    def __init__(self, rel_type, source_id, source_name, source_file, target_id, target_name, target_file, evidence_id):
        self.type = rel_type
        self.source_id = source_id
        self.source_name = source_name
        self.source_file = source_file
        self.target_id = target_id
        self.target_name = target_name
        self.target_file = target_file
        self.evidence_id = evidence_id


def _calls(source_id, source_name, source_file, target_id, target_name, target_file, evidence_id, line):
    rel = _rel("CALLS", source_id, source_name, source_file, target_id, target_name, target_file, evidence_id)
    rel.line = line
    return rel


def _rel(rel_type, source_id, source_name, source_file, target_id, target_name, target_file, evidence_id):
    return _Rel(rel_type, source_id, source_name, source_file, target_id, target_name, target_file, evidence_id)


class _Evidence:
    def __init__(self, evidence_id, file, start_line):
        self.id = evidence_id
        self.file = file
        self.start_line = start_line
        self.end_line = start_line


def _evidence(evidence_id, file, start_line):
    return _Evidence(evidence_id, file, start_line)
