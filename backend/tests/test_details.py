from app.analyzer.analyze import analyze
from app.analyzer.diagram import build_change_flow
from app.analyzer.fixture import load_oauth_snapshot
from app.explanation.details import build_details, render_details_markdown
from app.explanation.schema import ExplanationDocument
from app.github.comment import explain_marker, render_combined_comment


_TITLES = [
    "High-level areas affected",
    "Key Changes",
    "Risk Areas",
    "What changed",
    "Change flow",
    "Shared code",
    "Why a file outside the diff matters",
    "Tests",
    "Unchanged boundary",
    "Reviewer Attention",
    "Review questions",
]

_COMMENT_ORDER = [
    "### High-level areas affected",
    "### Key Changes",
    "### Risk Areas",
    "### What changed",
    "### Change flow",
    "### Shared code",
    "### Why a file outside the diff matters",
    "<summary>Tests</summary>",
    "<summary>Unchanged boundary</summary>",
    "### Reviewer Attention",
    "### Review questions",
]


def test_details_sections_use_stored_facts_and_name_gaps():
    result = analyze(load_oauth_snapshot())
    story = build_change_flow(result.symbols, result.relationships, result.evidences)
    details = build_details(
        symbols=result.symbols,
        relationships=result.relationships,
        evidences=result.evidences,
        claims=result.claims,
        sections=story["sections"],
        repo=result.evidences[0].repo if result.evidences else "fixture/oauth-ts",
        sha="b" * 40,
    )
    assert [section["title"] for section in details["sections"]] == _TITLES
    rows = {section["title"]: section["rows"] for section in details["sections"]}

    assert "System Impact" not in rows
    assert "Impact" not in rows

    assert "Change Overview" not in rows
    assert "Unknowns" not in rows

    areas = {row["label"]: row["value"] for row in rows["High-level areas affected"]}
    assert "API" in areas
    assert "Auth" in areas
    assert "Database" not in areas
    assert all(row["value"] != "none found" for row in rows["High-level areas affected"])
    assert "is an exported API" not in " ".join(areas.values())

    key_labels = [row["label"] for row in rows["Key Changes"]]
    changed_labels = [row["label"] for row in rows["What changed"]]
    assert "createSession" in key_labels
    assert set(label for label in key_labels if label != "More") <= set(changed_labels)
    assert any("/" in label for label in changed_labels)
    assert all("/" not in label for label in key_labels if label != "More")

    assert "Behavior Changes" not in rows
    assert "Behavioral Changes" not in rows

    risk_text = " ".join(row["value"] for row in rows["Risk Areas"]).lower()
    assert any(row["value"] == "no test reference is stored" for row in rows["Risk Areas"])
    assert any(
        "reaches changed symbol" in row["value"] or "not in the diff" in row["value"]
        for row in rows["Risk Areas"]
    )
    for banned in ("insecure", "broken", "risky", "approve", "score"):
        assert banned not in risk_text

    attention_rows = rows["Reviewer Attention"]
    attention = " ".join(row["value"] for row in attention_rows)
    assert any(row["label"] == "createSession" and row["value"] == "no test reference is stored" for row in attention_rows)
    assert any(row["label"] == "src/login.ts" and row["value"] == "login calls createSession" for row in attention_rows)
    assert any(
        row["label"] == "src/password.ts" and row["value"] == "reaches changed symbol createSession"
        for row in attention_rows
    )
    assert "exported in this pull request" not in attention
    assert "none found" not in attention
    for banned_label in ("API", "Auth", "Database", "Frontend", "Backend", "Dependencies", "Configuration"):
        assert banned_label not in {row["label"] for row in attention_rows}
    assert "Suggested review areas" not in [section["title"] for section in details["sections"]]
    assert not any(
        subsection.get("title") == "Suggested review areas"
        for section in details["sections"]
        for subsection in section.get("subsections") or []
    )

    changed = next(row for row in rows["What changed"] if row["label"] == "createSession")
    assert "src/session.ts" in changed["value"]
    assert ":" in changed["value"]

    shared = next(row for row in rows["Shared code"] if row["label"] == "createSession")
    assert shared["value"].startswith("callers: ")
    assert shared["value"].count(",") >= 1

    assert any(row["value"] == "no test reference" for row in rows["Tests"])
    assert rows["Review questions"] == [{"label": "Review", "value": "none found", "href": None}]
    question_text = " ".join(row["value"] for row in rows["Review questions"]).lower()
    assert "what test should reference" not in question_text
    assert "does this look correct" not in question_text

    for row in rows["Unchanged boundary"]:
        if row["value"].startswith("reaches changed symbol"):
            assert "unchanged" not in row["value"]
    assert any(row["value"] == "does not reach a changed symbol" for row in rows["Unchanged boundary"])
    assert any(row["label"].endswith(".ts") for row in rows["Why a file outside the diff matters"])

    body = render_combined_comment(
        document=ExplanationDocument(summary="createSession changed."),
        failure=None,
        repo_full_name="fixture/oauth-ts",
        pr_number=7,
        head_sha="b" * 40,
        app_base_url="http://explain.example",
        run_id="run",
        evidence_by_id={},
        change_flow=story["text"],
        bullets=["createSession changed."],
        mermaid=story["mermaid"],
        claims=result.claims,
        sections=story["sections"],
        evidence=result.evidences,
        symbols=result.symbols,
        relationships=result.relationships,
    )
    assert explain_marker("fixture/oauth-ts", 7) in body
    assert "view=details" not in body
    assert body.index("## Explain for") < body.index("## Details for") < body.index("## Review for")
    explain, rest = body.split("## Details for", 1)
    details, review = rest.split("## Review for", 1)
    assert "```mermaid" in explain
    assert "### Diagram" not in body
    assert "## Diagram" not in body
    # Details holds the old and new flow diagrams (above Key Changes); no other diagram.
    assert "```mermaid" not in details or "### Old flow vs New flow" in details
    assert "```mermaid" not in review
    assert "### Impact" not in details
    assert "### Impact" in explain
    assert "| Area | Reason | Evidence file |" not in details
    impact_md = explain.split("### Impact", 1)[1].split("## ", 1)[0]
    assert "impact summary is not available" in impact_md
    assert explain.index("### Behavioral Changes") < explain.index("### Impact")
    assert "- createSession changed." not in explain  # Explain has no summary section
    assert "### What changed" in details
    assert "createSession" in body
    detail_titles = _COMMENT_ORDER[: _COMMENT_ORDER.index("<summary>Unchanged boundary</summary>") + 1]
    places = [details.index(title) for title in detail_titles]
    assert places == sorted(places)
    assert "### Change Overview" not in details
    assert "Change Overview" not in details
    assert "### Unknowns" not in details
    assert "| Area | Names |" in details
    assert "| Change | Location |" in details
    assert "| Where | Why look |" in details
    assert "### Behavior Changes" not in details
    assert "### Behavioral Changes" in explain
    assert "### Behavioral Changes" not in details
    assert "### Change flow" not in explain
    assert explain.index("```mermaid") < explain.index("### Behavioral Changes")
    assert "### Reviewer Attention" not in details
    assert "### Review questions" not in details
    assert "### Reviewer Attention" in review
    assert "Suggested review areas" not in review
    assert "| Where | Why look |" not in review
    assert "### Trace" not in body
    assert "what test should reference" not in review.lower()
    attention_at = review.index("### Reviewer Attention")
    questions_at = review.index("### Review questions")
    assert attention_at < questions_at
    lowered = body.lower()
    assert "one-hop" not in lowered
    assert "insecure" not in lowered
    assert "score" not in lowered
    assert "production" not in lowered
    assert "broken" not in lowered
    assert "risky" not in lowered


class _Symbol:
    def __init__(self, name: str, path: str, *, changed: bool = True, exported: bool = False) -> None:
        self.id = name
        self.name = name
        self.kind = "function"
        self.file_path = path
        self.start_line = 1
        self.end_line = 4
        self.exported = exported
        self.changed = changed


class _Rel:
    def __init__(self, rel_type: str, source: str, target: str, source_file: str, target_file: str, evidence_id: str | None = None) -> None:
        self.type = rel_type
        self.source_id = source
        self.target_id = target
        self.source_name = source
        self.target_name = target
        self.source_file = source_file
        self.target_file = target_file
        self.evidence_id = evidence_id


class _Evidence:
    def __init__(self, evidence_id: str, file: str, start: int, end: int) -> None:
        self.id = evidence_id
        self.file = file
        self.start_line = start
        self.end_line = end


class _Claim:
    def __init__(self, kind: str, subject: str | None, text: str, evidence_ids: list[str] | None = None) -> None:
        self.kind = kind
        self.subject = subject
        self.text = text
        self.evidence_ids = evidence_ids or []


def test_details_omits_behavior_section_and_still_surfaces_risk():
    symbol = _Symbol("changedFn", "src/a.ts", exported=True)
    relationships = [
        _Rel("IMPORTS", "importer", "changedFn", "src/b.ts", "src/a.ts", "ev-import"),
        _Rel("CALLS", "caller", "changedFn", "src/c.ts", "src/a.ts", "ev-call"),
    ]
    evidences = [_Evidence("ev-call", "src/c.ts", 8, 8)]
    claims = [
        _Claim("symbol_changed", "changedFn", "changedFn changed in src/a.ts.", ["ev-call"]),
        _Claim("defines_api", "changedFn", "changedFn is an exported API in src/a.ts.", ["ev-call"]),
        _Claim("file_changed", "src/a.ts", "src/a.ts is changed in this pull request.", ["ev-call"]),
        _Claim("missing_test", "changedFn", "No test references changedFn."),
        _Claim(
            "reaches_changed",
            "src/b.ts",
            "src/b.ts is absent from the diff, but a call or import path reaches changed symbol changedFn, so its behavior is not unchanged.",
        ),
        _Claim("missing_test", "caller", "No test references caller."),
        _Claim("unknown_boundary", "Unknown", "No dependency facts are in this packet."),
        _Claim("unknown_boundary", None, "No external system facts are in this packet."),
    ]
    details = build_details(
        symbols=[symbol],
        relationships=relationships,
        evidences=evidences,
        claims=claims,
        sections=[],
        repo="acme/app",
        sha="a" * 40,
        document_unknowns=["No database or schema facts are in this packet."],
        review_questions=[
            "Is changedFn tested?",
            "Does this look correct?",
            "Does caller still pass the value changedFn returns?",
            "Should we approve this insecure change?",
        ],
    )
    rows = {section["title"]: section["rows"] for section in details["sections"]}
    assert "Behavior Changes" not in rows

    risk_labels = {row["label"] for row in rows["Risk Areas"]}
    assert "changedFn" in risk_labels
    assert "caller" not in risk_labels
    assert any(row["label"] == "src/b.ts" and row["value"] == "reaches changed symbol changedFn" for row in rows["Risk Areas"])
    assert all("insecure" not in row["value"] and "risky" not in row["value"] for row in rows["Risk Areas"])

    attention_labels = [row["label"] for row in rows["Reviewer Attention"]]
    assert "changedFn" in attention_labels
    assert any(row["label"] == "changedFn" and row["value"] == "no test reference is stored" for row in rows["Reviewer Attention"])
    assert any(row["label"] == "src/b.ts" and row["value"] == "reaches changed symbol changedFn" for row in rows["Reviewer Attention"])
    assert not any(row["label"] == "Unknown" for row in rows["Reviewer Attention"])
    for unknown_text in (
        "No database or schema facts are in this packet",
        "No dependency facts are in this packet",
        "No external system facts are in this packet",
    ):
        assert unknown_text not in [row["value"] for row in rows["Reviewer Attention"]]
        assert "Unknowns" not in rows
        assert unknown_text not in [row["value"] for section in rows.values() for row in section]
    assert not any("exported in this pull request" in row["value"] for row in rows["Reviewer Attention"])
    assert all(row["label"] != "Database" for row in rows["Reviewer Attention"])
    assert [row["value"] for row in rows["Review questions"]] == ["Does caller still pass the value changedFn returns?"]

    empty = build_details(
        symbols=[],
        relationships=[],
        evidences=[],
        claims=[],
        sections=[],
        repo="acme/app",
        sha="a" * 40,
    )
    empty_rows = {section["title"]: section["rows"] for section in empty["sections"]}
    assert "Behavior Changes" not in empty_rows
    assert "Change Overview" not in empty_rows
    assert empty_rows["Reviewer Attention"] == [{"label": "Inspect", "value": "none found", "href": None}]
    assert not any(
        subsection.get("title") == "Suggested review areas"
        for section in empty["sections"]
        for subsection in section.get("subsections") or []
    )


def test_change_overview_is_absent():
    symbols = [_Symbol(f"fn{i}", f"src/f{i}.ts") for i in range(7)]
    symbols.append(_Symbol("fn0b", "src/f0.ts"))
    claims = [_Claim("file_changed", f"src/f{i}.ts", "changed") for i in range(7)]
    claims += [_Claim("file_changed", f"pkg/extra{i}.txt", "changed") for i in range(3)]
    details = build_details(
        symbols=symbols,
        relationships=[_Rel("CALLS", "fn0", "other", "src/f0.ts", "src/other.ts", "ev-call")],
        evidences=[_Evidence("ev-call", "src/f0.ts", 2, 2)],
        claims=claims,
        sections=[],
        repo="acme/app",
        sha="a" * 40,
    )
    titles = [section["title"] for section in details["sections"]]
    assert "Change Overview" not in titles
    assert titles[0] == "High-level areas affected"
    markdown = render_details_markdown(details)
    assert "### Change Overview" not in markdown
    assert "Change Overview" not in markdown
    assert "### High-level areas affected" in markdown

    files_only = build_details(
        symbols=[],
        relationships=[],
        evidences=[],
        claims=[_Claim("file_changed", f"pkg/file{i}.txt", "changed") for i in range(7)],
        sections=[],
        repo="acme/app",
        sha="a" * 40,
    )
    assert "Change Overview" not in [section["title"] for section in files_only["sections"]]
    assert "Change Overview" not in render_details_markdown(files_only)


def test_system_impact_skips_test_paths_risk_is_deduped_and_long_sections_collapse():
    symbols = [
        _Symbol("explain", "backend/app/llm/provider.py"),
        _Symbol("create_llm_provider", "backend/app/llm/provider.py"),
        _Symbol("add", "backend/app/llm/provider.py"),
        _Symbol("_symbol_id", "backend/app/explanation/details.py"),
        _Symbol("_configured", "backend/app/llm/provider.py"),
    ]
    claims = [
        _Claim("file_changed", "backend/app/llm/provider.py", "backend/app/llm/provider.py is changed in this pull request."),
        _Claim("defines_api", "create_llm_provider", "create_llm_provider is an exported API in backend/app/llm/provider.py."),
        _Claim("tests", "create_llm_provider", "backend/tests/test_provider.py tests create_llm_provider."),
        _Claim("tests", "run", "pkg/run_test.py tests run."),
        _Claim("tests", "goRun", "service_test.go tests goRun."),
        _Claim("tests", "javaRun", "AppTest.java tests javaRun."),
        _Claim("tests", "javaRuns", "AppTests.java tests javaRuns."),
        _Claim("tests", "ui", "src/ui.test.tsx tests ui."),
        _Claim("tests", "uiSpec", "src/ui.spec.ts tests uiSpec."),
        _Claim("file_changed", "backend/tests/test_provider.py", "backend/tests/test_provider.py is changed in this pull request."),
        _Claim("missing_test", "explain", "No test references explain."),
        _Claim("missing_test", "create_llm_provider", "No test references create_llm_provider."),
        _Claim("missing_test", "add", "No test references add."),
        _Claim("missing_test", "_symbol_id", "No test references _symbol_id."),
        _Claim("missing_test", "_text", "No test references _text."),
        _Claim("missing_test", "_subject", "No test references _subject."),
        _Claim("missing_test", "__init__", "No test references __init__."),
        _Claim("missing_test", "_configured", "No test references _configured."),
        _Claim("ambiguous_call", "explain", "Call to explain at backend/app/llm/provider.py:1 is ambiguous across 2 definitions."),
        _Claim("ambiguous_call", "explain", "Call to explain at backend/app/llm/provider.py:8 is ambiguous across 2 definitions."),
        _Claim("ambiguous_call", "add", "Call to add at backend/app/llm/provider.py:2 is a member call and was not resolved to a function edge."),
        _Claim("ambiguous_call", "add", "Call to add at backend/app/llm/provider.py:3 is a member call and was not resolved to a function edge."),
        _Claim("ambiguous_call", "_symbol_id", "Call to _symbol_id at backend/app/explanation/details.py:1 is ambiguous across 3 definitions."),
        _Claim("ambiguous_call", "_text", "Call to _text at backend/app/analyzer/parse.py:1 is ambiguous across 3 definitions."),
        _Claim("ambiguous_call", "_subject", "Call to _subject at backend/app/explanation/details.py:2 is ambiguous across 2 definitions."),
        _Claim("ambiguous_call", "__repr__", "Call to __repr__ at backend/app/llm/provider.py:4 is ambiguous across 2 definitions."),
        _Claim("ambiguous_call", "_kind", "Call to _kind at backend/app/explanation/details.py:10 is ambiguous across 2 definitions."),
        _Claim("ambiguous_call", "_kind", "Call to _kind at backend/app/explanation/details.py:11 is ambiguous across 2 definitions."),
        _Claim("ambiguous_call", "list_comments", "Call to list_comments at backend/app/github/comment.py:1 is ambiguous across 3 definitions."),
        _Claim("ambiguous_call", "list_comments", "Call to list_comments at backend/app/github/comment.py:1 is ambiguous across 3 definitions."),
        _Claim("ambiguous_call", "list_comments", "Call to list_comments at backend/app/github/comment.py:9 is ambiguous across 3 definitions."),
        _Claim(
            "file_reason",
            "backend/app/worker.py",
            "backend/app/worker.py is not in the diff and matters because run calls create_llm_provider.",
        ),
        _Claim(
            "file_reason",
            "backend/app/worker.py",
            "backend/app/worker.py also shows up for another reason.",
        ),
        _Claim("unknown_boundary", "Unknown", "No dependency facts are in this packet."),
    ]
    claims += [
        _Claim("file_changed", f"backend/app/extra{i}.py", f"backend/app/extra{i}.py is changed in this pull request.")
        for i in range(22)
    ]
    claims += [
        _Claim("tests", f"fn{i}", f"backend/tests/test_fn{i}.py tests fn{i}.")
        for i in range(22)
    ]
    details = build_details(
        symbols=symbols,
        relationships=[],
        evidences=[],
        claims=claims,
        sections=[],
        repo="acme/app",
        sha="a" * 40,
        document_unknowns=["No database or schema facts are in this packet."],
    )
    rows = {section["title"]: section["rows"] for section in details["sections"]}
    assert "Unknowns" not in rows
    assert "Change Overview" not in rows

    assert "System Impact" not in rows

    risk = rows["Risk Areas"]
    risk_labels = [row["label"] for row in risk]
    for banned in (
        "add",
        "_symbol_id",
        "_text",
        "_subject",
        "__init__",
        "__repr__",
        "_kind",
        "list_comments",
        "push",
        "statements",
        "upgrade",
        "main",
    ):
        assert banned not in risk_labels
    assert risk_labels.count("explain") == 1
    assert risk_labels.count("create_llm_provider") == 1
    assert risk_labels.count("backend/app/worker.py") == 1
    assert "_configured" not in risk_labels
    assert all(row["value"] != "none found" for row in risk)
    explain = next(row for row in risk if row["label"] == "explain")
    assert explain["value"] == "no test reference is stored"
    worker = next(row for row in risk if row["label"] == "backend/app/worker.py")
    assert "not in the diff" in worker["value"]
    assert "another reason" not in worker["value"]

    assert len(rows["Tests"]) > 20
    markdown = render_details_markdown(details)
    assert "### Unknowns" not in markdown
    assert "No dependency facts are in this packet" not in markdown
    assert "No database or schema facts are in this packet" not in markdown
    tests_md = markdown.split("<summary>Tests</summary>", 1)[1].split("</details>", 1)[0]
    assert "more</summary>" not in tests_md
    assert "<details open" not in markdown
    assert "test_fn0.py" in tests_md
    assert f"test_fn{len(rows['Tests']) - 1}.py" in tests_md or "fn21" in tests_md
    boundary_md = markdown.split("<summary>Unchanged boundary</summary>", 1)[1].split("</details>", 1)[0]
    assert boundary_md.strip()
    assert "### Impact" not in markdown
    shared_md = markdown.split("### Shared code", 1)[1].split("<details>", 1)[0]
    assert "<details>" not in shared_md
