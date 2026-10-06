from app.explanation.details import build_details, render_details_markdown


class _Symbol:
    def __init__(self, name, file_path, *, changed=True):
        self.kind = "function"
        self.name = name
        self.file_path = file_path
        self.changed = changed
        self.start_line = 1
        self.end_line = 1


class _Claim:
    def __init__(self, kind, subject, text, evidence_ids=None):
        self.kind = kind
        self.subject = subject
        self.text = text
        self.evidence_ids = evidence_ids or []


def test_details_sections_drop_private_python_and_trim_mixed_sentences():
    symbols = [
        _Symbol("explain", "backend/app/llm/provider.py"),
        _Symbol("create_llm_provider", "backend/app/llm/provider.py"),
        _Symbol("_configured", "backend/app/llm/provider.py"),
        _Symbol("_openai_provider", "backend/app/llm/provider.py"),
    ]
    reach = (
        "backend/app/seed.py is absent from the diff, but a call or import path reaches "
        "changed symbol _configured, _openai_provider, create_llm_provider, so its behavior is not unchanged."
    )
    claims = [
        _Claim("symbol_changed", "explain", "explain changed in backend/app/llm/provider.py."),
        _Claim("symbol_changed", "_configured", "_configured changed in backend/app/llm/provider.py."),
        _Claim("missing_test", "_configured", "No test references _configured."),
        _Claim("missing_test", "explain", "No test references explain."),
        _Claim("reaches_changed", "backend/app/seed.py", reach),
        _Claim(
            "file_reason",
            "backend/app/worker.py",
            "backend/app/worker.py is not in the diff and matters because _provider_or_failure calls create_llm_provider.",
        ),
        _Claim("tests", "create_llm_provider", "backend/tests/test_provider.py tests create_llm_provider."),
    ]
    details = build_details(
        symbols=symbols,
        relationships=[],
        evidences=[],
        claims=claims,
        sections=[],
        repo="acme/app",
        sha="b" * 40,
    )
    blob = render_details_markdown(details)
    for banned in ("_configured", "_openai_provider", "_provider_or_failure"):
        assert banned not in blob
    assert "create_llm_provider" in blob
    assert "explain" in blob
    assert "create_llm_provider, so its behavior is not unchanged." in blob or "create_llm_provider" in blob


def test_details_has_no_review_sections_and_hides_private_python():
    symbols = [
        _Symbol("explain", "backend/app/llm/provider.py"),
        _Symbol("create_llm_provider", "backend/app/llm/provider.py"),
        _Symbol("_configured", "backend/app/llm/provider.py"),
    ]
    claims = [
        _Claim("symbol_changed", "explain", "explain changed in backend/app/llm/provider.py."),
        _Claim("missing_test", "_configured", "No test references _configured."),
        _Claim("missing_test", "explain", "No test references explain."),
        _Claim(
            "reaches_changed",
            "backend/app/seed.py",
            "backend/app/seed.py reaches changed symbol _configured, create_llm_provider.",
        ),
    ]
    review_questions = [
        "Does create_llm_provider still work when _configured is unset?",
        "Should _openai_provider stay internal?",
    ]
    details = build_details(
        symbols=symbols,
        relationships=[],
        evidences=[],
        claims=claims,
        sections=[],
        repo="acme/app",
        sha="b" * 40,
        review_questions=review_questions,
    )
    # Review content moved to the Review tab's report; Details no longer carries it.
    titles = {section["title"] for section in details["sections"]}
    assert not titles & {"Reviewer Attention", "Review questions", "Tests", "Unchanged boundary"}
    blob = render_details_markdown(details)
    for banned in ("_configured", "_openai_provider", "_provider_or_failure"):
        assert banned not in blob
