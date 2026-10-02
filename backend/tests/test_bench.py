import json
from pathlib import Path

from app.explanation.schema import ExplanationPacket
from app.llm.bench import DISCLAIMER, load_fixtures, score_explanation
from tests.fakes import document_from_packet

ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "bench"


def test_bench_scores_grounding_without_a_quality_score():
    fixtures = {item["id"]: item for item in load_fixtures(ROOT)}
    assert set(fixtures) == {
        "oauth-create-session",
        "dependency-only",
        "diff-only",
        "truncated-fanout",
    }
    oauth = fixtures["oauth-create-session"]
    packet = ExplanationPacket.model_validate(oauth["packet"])
    good = document_from_packet(packet).model_dump_json()
    score = score_explanation(
        packet,
        good,
        constraints=oauth["constraints"],
        latency_ms=15,
        peak_memory_bytes=2048,
    )
    assert score["disclaimer"] == DISCLAIMER
    assert "quality_score" not in score
    assert score["schema_valid"] is True
    assert score["evidence_grounding_rate"] == 1
    assert score["impacted_callers_present"] is True
    assert score["forbidden_names_absent"] is True
    assert score["required_substrings_present"] is True
    assert score["latency_ms"] == 15
    assert score["peak_memory_bytes"] == 2048

    planted = json.loads(good)
    planted["change_flow"].append(
        {
            "epistemic": "FACT",
            "text": "inventedFourthCaller calls createSession.",
            "claim_ids": ["cl_missing"],
            "evidence_ids": [],
        }
    )
    bad = score_explanation(
        packet,
        json.dumps(planted),
        constraints=oauth["constraints"],
        latency_ms=15,
        peak_memory_bytes=2048,
    )
    assert bad["forbidden_names_absent"] is False

    deps = fixtures["dependency-only"]
    dep_packet = ExplanationPacket.model_validate(deps["packet"])
    dep_score = score_explanation(
        dep_packet,
        document_from_packet(dep_packet).model_dump_json(),
        constraints=deps["constraints"],
        latency_ms=3,
        peak_memory_bytes=10,
    )
    assert dep_score["call_graph_ok"] is True
    fake_calls = document_from_packet(dep_packet).model_dump()
    fake_calls["summary"] = "login calls createSession"
    assert score_explanation(
        dep_packet,
        json.dumps(fake_calls),
        constraints=deps["constraints"],
        latency_ms=3,
        peak_memory_bytes=10,
    )["call_graph_ok"] is False

    diff_packet = ExplanationPacket.model_validate(fixtures["diff-only"]["packet"])
    diff_score = score_explanation(
        diff_packet,
        document_from_packet(diff_packet).model_dump_json(),
        constraints=fixtures["diff-only"]["constraints"],
        latency_ms=1,
        peak_memory_bytes=1,
    )
    assert diff_score["required_substrings_present"] is True
    assert diff_score["call_graph_ok"] is True

    fan_packet = ExplanationPacket.model_validate(fixtures["truncated-fanout"]["packet"])
    fan_score = score_explanation(
        fan_packet,
        document_from_packet(fan_packet).model_dump_json(),
        constraints=fixtures["truncated-fanout"]["constraints"],
        latency_ms=9,
        peak_memory_bytes=9,
    )
    assert fan_score["required_substrings_present"] is True
    assert fan_score["forbidden_names_absent"] is True
    assert fan_score["impacted_callers_present"] is True
