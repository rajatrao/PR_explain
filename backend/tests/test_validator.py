import json

from app.explanation.schema import (
    ClaimRef,
    EvidenceRef,
    ExplanationPacket,
    RevisionRef,
    SymbolRef,
)
from app.explanation.validate import validate_response


def _packet() -> ExplanationPacket:
    return ExplanationPacket(
        revision=RevisionRef(
            repository="acme/app",
            pr_number=1,
            head_sha="b" * 40,
            base_sha="a" * 40,
        ),
        claims=[
            ClaimRef(
                id="cl_fact",
                epistemic="FACT",
                kind="calls",
                text="login calls createSession.",
                subject="createSession",
                evidence_ids=["ev_login"],
            ),
            ClaimRef(
                id="cl_inf",
                epistemic="INFERENCE",
                kind="behavior_unchanged",
                text="src/health.ts behavior is unchanged because no call or import path reaches a changed symbol.",
                subject="src/health.ts",
                evidence_ids=["ev_health"],
            ),
        ],
        symbols=[
            SymbolRef(
                id="sym_login",
                name="login",
                kind="function",
                file_path="src/login.ts",
                start_line=1,
                end_line=4,
                exported=True,
                changed=False,
            ),
            SymbolRef(
                id="sym_create",
                name="createSession",
                kind="function",
                file_path="src/session.ts",
                start_line=1,
                end_line=4,
                exported=True,
                changed=True,
            ),
        ],
        evidence=[
            EvidenceRef(
                id="ev_login",
                type="source_span",
                repo="acme/app",
                commit_sha="b" * 40,
                file="src/login.ts",
                start_line=4,
                end_line=4,
                symbol="createSession",
                description="login calls createSession.",
            ),
            EvidenceRef(
                id="ev_health",
                type="absent_file",
                repo="acme/app",
                commit_sha="b" * 40,
                file="src/health.ts",
                description="src/health.ts does not appear in the compare diff.",
            ),
        ],
    )


def _statement(**overrides):
    payload = {
        "epistemic": "FACT",
        "text": "login calls createSession.",
        "claim_ids": ["cl_fact"],
        "evidence_ids": ["ev_login"],
    }
    payload.update(overrides)
    return payload


def _document(**overrides):
    payload = {
        "summary": "Session creation changed.",
        "change_flow": [_statement()],
        "impacts": [],
        "important_changes": [],
        "tests": [],
        "unchanged": [],
        "unknowns": [],
        "review_questions": [],
    }
    payload.update(overrides)
    return payload


def test_invented_id_is_dropped():
    raw = _document(
        change_flow=[
            _statement(claim_ids=["cl_fact", "cl_invented"], evidence_ids=["ev_login", "ev_invented"]),
            _statement(
                text="nobody home",
                claim_ids=["cl_missing"],
                evidence_ids=[],
            ),
        ]
    )
    result = validate_response(json.dumps(raw), _packet())
    assert result.ok
    assert result.document is not None
    ids = [claim_id for statement in result.document.statements() for claim_id in statement.claim_ids]
    assert "cl_invented" not in ids
    assert "cl_missing" not in ids
    assert ids == ["cl_fact"]


def test_inference_promoted_to_fact_is_downgraded():
    raw = _document(
        change_flow=[
            _statement(
                epistemic="FACT",
                text="src/health.ts behavior is unchanged because no call or import path reaches a changed symbol.",
                claim_ids=["cl_inf"],
                evidence_ids=["ev_health"],
            )
        ]
    )
    result = validate_response(json.dumps(raw), _packet())
    assert result.ok
    assert result.document is not None
    assert result.document.change_flow[0].epistemic == "INFERENCE"


def test_defect_verdict_is_dropped():
    raw = _document(
        review_questions=[
            _statement(
                epistemic="UNKNOWN",
                text="This is insecure.",
                claim_ids=["cl_fact"],
                evidence_ids=["ev_login"],
            ),
            _statement(
                epistemic="UNKNOWN",
                text="Which caller has no test?",
                claim_ids=["cl_fact"],
                evidence_ids=["ev_login"],
            ),
        ]
    )
    result = validate_response(json.dumps(raw), _packet())
    assert result.ok
    assert result.document is not None
    assert len(result.document.review_questions) == 1
    assert "insecure" not in result.document.review_questions[0].text


def test_empty_document_is_a_failure_and_score_is_stripped():
    raw = _document(
        summary="Fine",
        change_flow=[_statement(claim_ids=["cl_nope"], evidence_ids=[])],
        score=12,
    )
    result = validate_response(json.dumps(raw), _packet())
    assert not result.ok
    assert any("every statement was dropped" in error for error in result.errors)
    kept = _document(score=99, extra_field=True)
    parsed = validate_response(json.dumps(kept), _packet())
    assert parsed.ok
    dumped = parsed.document.model_dump()
    assert "score" not in dumped
    assert "extra_field" not in dumped


def test_invented_symbol_drops_the_statement():
    raw = _document(
        change_flow=[
            _statement(text="inventedFourthCaller calls createSession."),
        ]
    )
    result = validate_response(json.dumps(raw), _packet())
    assert not result.ok


def test_area_words_are_not_treated_as_named_symbols():
    raw = _document(summary="The API and UI changed.", change_flow=[_statement(text="The API calls createSession.")])
    result = validate_response(json.dumps(raw), _packet())
    assert result.ok
    assert result.document is not None
    assert result.document.summary == "The API and UI changed."
    assert result.document.change_flow[0].text == "The API calls createSession."
    assert not any("named symbols" in error for error in result.errors)


def test_empty_statements_do_not_blame_area_words():
    raw = _document(summary="The API and UI changed.", change_flow=[])
    result = validate_response(json.dumps(raw), _packet())
    assert not result.ok
    assert any("every statement was dropped" in error for error in result.errors)
    assert not any("API" in error or "UI" in error for error in result.errors)


def test_summary_naming_an_absent_function_is_cleared():
    raw = _document(summary="The API and UI call inventedFourthCaller.")
    result = validate_response(json.dumps(raw), _packet())
    assert result.ok
    assert result.document is not None
    assert result.document.summary == ""
    assert result.document.change_flow
    joined = " ".join(result.errors)
    assert "inventedFourthCaller" in joined
    assert "API" not in joined
    assert "UI" not in joined


def test_packet_symbol_that_looks_like_an_area_word_is_still_dropped():
    packet = _packet()
    packet.symbols.append(
        SymbolRef(
            id="sym_api",
            name="API",
            kind="function",
            file_path="src/api.ts",
            start_line=1,
            end_line=2,
            exported=True,
            changed=True,
        )
    )
    raw = _document(change_flow=[_statement(text="API changed.")])
    result = validate_response(json.dumps(raw), packet)
    assert not result.ok
    assert result.document is not None
    assert result.document.change_flow == []
