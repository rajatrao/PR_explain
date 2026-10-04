from app.analyzer.analyze import analyze
from app.analyzer.fixture import load_oauth_snapshot
from app.api import map_stored_explanations
from app.explanation.narrate import compose_document, explain_bullets
from app.explanation.select import build_packet
from app.explanation.validate import validate_response

DEPTHS = ("quick", "deep")


def _packets():
    result = analyze(load_oauth_snapshot())
    snapshot = load_oauth_snapshot()
    return result, {
        depth: build_packet(result, snapshot, depth, 32000)
        for depth in DEPTHS
    }


def test_depths_select_different_claims():
    _result, packets = _packets()
    kinds = {depth: {claim.kind for claim in packet.claims} for depth, packet in packets.items()}
    assert "behavior_unchanged" not in kinds["quick"]
    assert "calls" in kinds["deep"]
    assert "defines_api" not in kinds["quick"]
    assert "unknown_boundary" in kinds["deep"]
    assert any(claim.kind == "unknown_boundary" and "database" in claim.text for claim in packets["deep"].claims)
    assert any(claim.kind == "unknown_boundary" and "external" in claim.text for claim in packets["deep"].claims)
    assert not any(rel.type == "CALLS" for rel in packets["quick"].relationships)
    assert any(rel.type == "CALLS" for rel in packets["deep"].relationships)


def test_depth_documents_read_differently_and_stay_grounded():
    result, packets = _packets()
    unchanged = [symbol.name for symbol in result.symbols if symbol.kind == "function" and not symbol.changed]
    documents = {}
    texts = {}
    for depth, packet in packets.items():
        document = compose_document(packet)
        checked = validate_response(document.model_dump_json(), packet)
        assert checked.ok, checked.errors
        assert checked.document is not None
        documents[depth] = checked.document
        texts[depth] = "\n".join(
            [checked.document.summary, *[statement.text for statement in checked.document.statements()]]
        )
    assert set(texts) == {"quick", "deep"}
    assert len(set(texts.values())) == 2
    assert "Major areas:" in texts["quick"]
    mentioned = [name for name in unchanged if name in texts["quick"]]
    assert len(mentioned) < len(unchanged)
    for text in texts.values():
        lowered = text.lower()
        assert "one-hop" not in lowered
        assert "one hop" not in lowered
        assert "second hop" not in lowered
    assert "calls" in texts["deep"]
    assert "login" in texts["deep"]
    assert ":" in texts["deep"]
    assert "No test references" in texts["deep"]
    assert "database" in texts["deep"].lower()
    assert "external" in texts["deep"].lower()
    quick_statements = documents["quick"].statements()
    assert len(quick_statements) <= 2
    assert documents["deep"].tests
    assert documents["deep"].unknowns
    assert documents["deep"].change_flow


def test_explain_bullets_are_separate_lines_from_claims():
    result = analyze(load_oauth_snapshot())
    bullets = explain_bullets(result.claims, result.symbols)
    assert any(item.endswith("changed.") or "more changed symbols" in item for item in bullets)
    assert any(item.startswith("Major areas:") for item in bullets)
    assert all("\n" not in item for item in bullets)
    lowered = " ".join(bullets).lower()
    assert "one-hop" not in lowered
    assert "one hop" not in lowered


def test_old_developer_document_is_shown_as_deep_until_a_new_deep_exists():
    developer = {
        "depth": "developer",
        "status": "succeeded",
        "provider": "fake",
        "model": "old",
        "error": None,
        "document": {"summary": "old developer"},
    }
    architecture = {
        "depth": "architecture",
        "status": "succeeded",
        "provider": "fake",
        "model": "old",
        "error": None,
        "document": {"summary": "old architecture"},
    }
    visible = map_stored_explanations({"developer": developer, "architecture": architecture})
    assert set(visible) == {"deep"}
    assert visible["deep"]["depth"] == "deep"
    assert visible["deep"]["document"]["summary"] == "old developer"

    regenerated = map_stored_explanations(
        {
            "developer": developer,
            "architecture": architecture,
            "deep": {
                "depth": "deep",
                "status": "succeeded",
                "provider": "fake",
                "model": "new",
                "error": None,
                "document": {"summary": "new deep"},
            },
            "quick": {
                "depth": "quick",
                "status": "succeeded",
                "provider": "fake",
                "model": "new",
                "error": None,
                "document": {"summary": "quick story"},
            },
        }
    )
    assert set(regenerated) == {"quick", "deep"}
    assert regenerated["deep"]["document"]["summary"] == "new deep"
    assert regenerated["quick"]["document"]["summary"] == "quick story"
