from app.analyzer.analyze import analyze
from app.analyzer.diagram import build_change_flow
from app.analyzer.fixture import load_oauth_snapshot
from app.explanation.details import build_details
from app.explanation.schema import ExplanationDocument
from app.github.comment import explain_marker, render_combined_comment


def _suggested(details: dict) -> dict:
    section = next(item for item in details["sections"] if item["title"] == "Reviewer Attention")
    return next(item for item in section["subsections"] if item["title"] == "Suggested review areas")

_TITLES = [
    "Change Overview",
    "High-level areas affected",
    "Key Changes",
    "Behavior Changes",
    "Risk Areas",
    "What changed",
    "Change flow",
    "Impact",
    "Shared code",
    "Tests",
    "Unchanged boundary",
    "Why a file outside the diff matters",
    "Unknowns",
    "Reviewer Attention",
    "Review questions",
]

_COMMENT_ORDER = [
    "### Change Overview",
    "### High-level areas affected",
    "### Key Changes",
    "### Behavior Changes",
    "### Risk Areas",
    "### What changed",
    "### Change flow",
    "### Impact",
    "### Shared code",
    "### Tests",
    "### Unchanged boundary",
    "### Why a file outside the diff matters",
    "### Unknowns",
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

    impact = {row["label"]: row["value"] for row in rows["Impact"]}
    assert impact["Database"] == "none found"
    assert impact["Frontend"] == "none found"
    assert impact["Backend"] == "none found"
    assert impact["Configuration"] == "none found"
    assert any(row["label"] == "createSession" and "API" in row["value"] or "exported" in row["value"] for row in rows["Impact"])
    linked = [row for row in rows["Impact"] if row.get("href")]
    assert linked
    for row in linked:
        assert row.get("evidence")
        assert row["evidence"] != row["value"]
        assert row["evidence"].split(":")[0] in row["href"]
    gaps = [row for row in rows["Impact"] if row["value"] == "none found"]
    assert gaps
    assert all(not row.get("evidence") for row in gaps)

    overview = " ".join(row["value"] for row in rows["Change Overview"])
    assert "createSession" in overview
    assert "src/" in overview
    assert "calls" in overview

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

    behavior = rows["Behavior Changes"]
    assert any("calls " in row["label"] for row in behavior)
    assert all("import" not in row["label"].lower() for row in behavior)
    assert all(row["value"] != "none found" for row in behavior)

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
    suggested = _suggested(details)
    assert suggested["title"] == "Suggested review areas"
    assert "Suggested review areas" not in [section["title"] for section in details["sections"]]
    suggested_labels = [row["label"] for row in suggested["rows"]]
    for banned_label in ("API", "Auth", "Database", "Frontend", "Backend", "Dependencies", "Configuration"):
        assert banned_label not in suggested_labels
    assert any(row["label"] == "createSession" and row["value"] == "no test reference is stored" for row in suggested["rows"])
    assert any(row["label"] == "src/login.ts" and row["value"] == "login calls createSession" for row in suggested["rows"])
    assert any(
        row["label"] == "src/password.ts" and row["value"] == "reaches changed symbol createSession"
        for row in suggested["rows"]
    )
    suggested_text = " ".join(row["value"] for row in suggested["rows"]).lower()
    for banned in ("insecure", "broken", "risky", "approve", "score"):
        assert banned not in suggested_text

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
    assert "```mermaid" not in details
    assert "```mermaid" not in review
    assert "### Impact" in details
    assert "| Area | Reason | Evidence file |" in details
    impact_md = details.split("### Impact", 1)[1].split("### ", 1)[0]
    assert "[is changed" not in impact_md
    assert "### What changed" in details
    assert "createSession" in body
    detail_titles = _COMMENT_ORDER[: _COMMENT_ORDER.index("### Unknowns") + 1]
    places = [details.index(title) for title in detail_titles]
    assert places == sorted(places)
    assert "| Area | Names |" in details
    assert "| Change | Location |" in details
    assert "| Call | Evidence |" in details
    assert "| Where | Why look |" in details
    behavior_md = details.split("### Behavior Changes", 1)[1].split("### ", 1)[0]
    assert "calls " in behavior_md
    assert "import" not in behavior_md.lower()
    assert "### Reviewer Attention" not in details
    assert "### Review questions" not in details
    assert "### Reviewer Attention" in review
    assert "#### Suggested review areas" in review
    assert "### Suggested review areas" not in body.replace("#### Suggested review areas", "")
    assert "| Where | Why look |" in review
    assert "### Trace" not in body
    assert "what test should reference" not in review.lower()
    attention_at = review.index("### Reviewer Attention")
    suggested_at = review.index("#### Suggested review areas")
    questions_at = review.index("### Review questions")
    assert attention_at < suggested_at < questions_at
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


def test_behavior_changes_use_stored_calls_and_name_a_gap():
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
    behavior = rows["Behavior Changes"]
    assert [row["label"] for row in behavior] == ["caller calls changedFn"]
    assert behavior[0]["value"] == "src/c.ts:8"
    assert "import" not in behavior[0]["label"].lower()

    risk_labels = {row["label"] for row in rows["Risk Areas"]}
    assert "changedFn" in risk_labels
    assert "caller" not in risk_labels
    assert any(row["label"] == "src/b.ts" and row["value"] == "reaches changed symbol changedFn" for row in rows["Risk Areas"])
    assert all("insecure" not in row["value"] and "risky" not in row["value"] for row in rows["Risk Areas"])

    attention_labels = [row["label"] for row in rows["Reviewer Attention"]]
    assert "changedFn" in attention_labels
    assert any(row["label"] == "changedFn" and row["value"] == "no test reference is stored" for row in rows["Reviewer Attention"])
    assert any(row["label"] == "src/b.ts" and row["value"] == "reaches changed symbol changedFn" for row in rows["Reviewer Attention"])
    assert any(row["label"] == "Unknown" and row["value"] == "No database or schema facts are in this packet" for row in rows["Reviewer Attention"])
    assert not any("exported in this pull request" in row["value"] for row in rows["Reviewer Attention"])
    assert all(row["label"] != "Database" for row in rows["Reviewer Attention"])
    suggested_rows = _suggested(details)["rows"]
    assert any(row["label"] == "changedFn" and row["value"] == "no test reference is stored" for row in suggested_rows)
    assert any(row["label"] == "src/b.ts" and row["value"] == "reaches changed symbol changedFn" for row in suggested_rows)
    assert all(row["label"] not in {"API", "Database", "Frontend", "Backend", "Auth"} for row in suggested_rows)
    assert "caller" not in {row["label"] for row in suggested_rows}
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
    assert empty_rows["Behavior Changes"] == [{"label": "Behavior", "value": "none found", "href": None}]
    assert empty_rows["Change Overview"] == [{"label": "Overview", "value": "none found", "href": None}]
    assert empty_rows["Reviewer Attention"] == [{"label": "Inspect", "value": "none found", "href": None}]
    assert _suggested(empty)["rows"] == []
