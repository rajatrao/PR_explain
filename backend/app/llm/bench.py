"""Score narration quality for a frozen packet. This is a model eval, not a pull-request score."""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from app.explanation.assemble import build_user_message, system_prompt
from app.explanation.schema import ExplanationDocument, ExplanationPacket
from app.explanation.validate import validate_response
from app.llm.provider import ExplainRequest, create_llm_provider

DISCLAIMER = "This is a model eval. It is not a pull-request quality score."


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_fixtures(directory: Path) -> list[dict]:
    fixtures = []
    for path in sorted(directory.glob("*.json")):
        fixtures.append(json.loads(path.read_text(encoding="utf-8")))
    return fixtures


def score_explanation(
    packet: ExplanationPacket,
    raw: str,
    *,
    constraints: dict,
    latency_ms: int,
    peak_memory_bytes: int,
) -> dict:
    schema_valid = False
    document = None
    try:
        payload = json.loads(raw)
        document = ExplanationDocument.model_validate(payload)
        schema_valid = True
    except (json.JSONDecodeError, ValidationError):
        schema_valid = False
    validation = validate_response(raw, packet)
    text = ""
    statements = []
    if document is not None:
        statements = document.statements()
        text = "\n".join([document.summary, *[statement.text for statement in statements]])
    claims = {claim.id: claim for claim in packet.claims}
    cited_evidence = [evidence_id for statement in statements for evidence_id in statement.evidence_ids]
    grounded = [item for item in cited_evidence if any(evidence.id == item for evidence in packet.evidence)]
    grounding_rate = (len(grounded) / len(cited_evidence)) if cited_evidence else 0.0
    accurate = 0
    considered = 0
    for statement in statements:
        cited = [claims[item] for item in statement.claim_ids if item in claims]
        if not cited:
            continue
        considered += 1
        if statement.epistemic == "FACT":
            ok = any(claim.epistemic == "FACT" for claim in cited) and not all(
                claim.epistemic != "FACT" for claim in cited
            )
        elif statement.epistemic == "INFERENCE":
            ok = any(claim.epistemic == "INFERENCE" for claim in cited) or all(
                claim.epistemic != "FACT" for claim in cited
            )
        else:
            ok = statement.epistemic == "UNKNOWN"
        accurate += int(ok)
    label_accuracy = (accurate / considered) if considered else 0.0
    unknown_hits = 0
    unknowns = packet.unknowns
    lowered = text.lower()
    for unknown in unknowns:
        token = (unknown.text or "").split()[-1].strip(".").lower() if unknown.text else ""
        if token and token in lowered:
            unknown_hits += 1
    unknown_recall = (unknown_hits / len(unknowns)) if unknowns else 1.0
    required = constraints.get("required_names") or []
    forbidden = constraints.get("forbidden_names") or []
    callers_present = all(name in text for name in required)
    forbidden_absent = all(name not in text for name in forbidden)
    required_bits = constraints.get("required_substrings") or []
    substrings_present = all(bit in text for bit in required_bits)
    call_graph_ok = True
    if constraints.get("forbid_call_graph"):
        call_graph_ok = " calls " not in text
    return {
        "disclaimer": DISCLAIMER,
        "schema_valid": schema_valid and validation.ok,
        "evidence_grounding_rate": grounding_rate,
        "invented_symbol_drops": validation.dropped,
        "fact_inference_accuracy": label_accuracy,
        "unknown_recall": unknown_recall,
        "impacted_callers_present": callers_present,
        "forbidden_names_absent": forbidden_absent,
        "required_substrings_present": substrings_present,
        "call_graph_ok": call_graph_ok,
        "latency_ms": latency_ms,
        "peak_memory_bytes": peak_memory_bytes,
    }


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return int(value)
    return int(value) * 1024


def run_bench(directory: Path, report_path: Path, settings) -> dict:
    provider = create_llm_provider(settings)
    results = []
    for fixture in load_fixtures(directory):
        packet = ExplanationPacket.model_validate(fixture["packet"])
        request = ExplainRequest(
            packet=packet,
            depth=packet.depth,
            system_prompt=system_prompt(),
            user_prompt=build_user_message(packet),
            json_schema=ExplanationDocument.model_json_schema(),
        )
        before = peak_rss_bytes()
        started = time.perf_counter()
        result = provider.explain(request)
        latency_ms = int((time.perf_counter() - started) * 1000)
        results.append(
            {
                "fixture": fixture["id"],
                "model": result.model,
                "score": score_explanation(
                    packet,
                    result.content,
                    constraints=fixture.get("constraints") or {},
                    latency_ms=latency_ms,
                    peak_memory_bytes=max(before, peak_rss_bytes()),
                ),
            }
        )
    report = {"disclaimer": DISCLAIMER, "results": results}
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    from app.config import get_settings

    parser = argparse.ArgumentParser(description="Score frozen explanation packets")
    parser.add_argument("--packets", default=str(repo_root() / "fixtures" / "bench"))
    parser.add_argument("--report", default="bench-report.json")
    args = parser.parse_args()
    report = run_bench(Path(args.packets), Path(args.report), get_settings())
    print(json.dumps({"disclaimer": report["disclaimer"], "fixtures": len(report["results"])}))


if __name__ == "__main__":
    main()
