from app.analyzer.types import Claim
from app.revision.delta import compare_claim_sets


def _claim(text: str, kind: str = "calls") -> Claim:
    return Claim(id=text, epistemic="FACT", kind=kind, text=text, subject="createSession")


def test_compare_claim_sets_reports_what_is_new():
    previous = [_claim("login calls createSession."), _claim("old edge", kind="tests")]
    current = [_claim("login calls createSession."), _claim("refreshToken calls createSession.")]
    delta = compare_claim_sets(previous, current)
    assert delta["unchanged_count"] == 1
    assert [item["text"] for item in delta["added"]] == ["refreshToken calls createSession."]
    assert [item["text"] for item in delta["removed"]] == ["old edge"]
