from app.analyzer.analyze import analyze
from app.analyzer.fixture import load_oauth_snapshot
from app.explanation.narrate import compose_document, explain_bullets
from app.explanation.select import build_packet
from app.explanation.validate import validate_response


def test_packet_holds_changed_code_and_its_facts():
    result = analyze(load_oauth_snapshot())
    packet = build_packet(result, load_oauth_snapshot(), 32000)
    kinds = {claim.kind for claim in packet.claims}
    assert "behavior_unchanged" not in kinds and "defines_api" not in kinds
    assert not packet.relationships
    assert "depth" not in packet.model_dump()


def test_explanation_document_is_grounded():
    result = analyze(load_oauth_snapshot())
    packet = build_packet(result, load_oauth_snapshot(), 32000)
    checked = validate_response(compose_document(packet).model_dump_json(), packet)
    assert checked.ok, checked.errors
    text = "\n".join([checked.document.summary, *[s.text for s in checked.document.statements()]]).lower()
    assert "Major areas:".lower() in text
    for jargon in ("one-hop", "one hop", "second hop"):
        assert jargon not in text


def test_explain_bullets_are_separate_lines_from_claims():
    result = analyze(load_oauth_snapshot())
    bullets = explain_bullets(result.claims, result.symbols)
    assert any(item.endswith("changed.") or "more changed symbols" in item for item in bullets)
    assert any(item.startswith("Major areas:") for item in bullets)
    assert all("\n" not in item for item in bullets)
    lowered = " ".join(bullets).lower()
    assert "one-hop" not in lowered
    assert "one hop" not in lowered
