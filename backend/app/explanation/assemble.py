from __future__ import annotations

import json
from pathlib import Path

from app.explanation.schema import ExplanationDepth, ExplanationDocument, ExplanationPacket

PROMPT_VERSION = "v13"
_PROMPTS = Path(__file__).resolve().parent / "prompts"
_BLOCKS = (
    "REPOSITORY FACTS",
    "CHANGE GRAPH",
    "EVIDENCE",
    "IMPACT ANALYSIS",
    "UNKNOWN INFORMATION",
    "BEHAVIOR FACTS",
    "IMPACT FACTS",
    "TASK",
    "OUTPUT SCHEMA",
)


def system_prompt() -> str:
    return (_PROMPTS / "system.md").read_text(encoding="utf-8").strip()


def depth_task(depth: ExplanationDepth) -> str:
    return (_PROMPTS / f"{depth}.md").read_text(encoding="utf-8").strip()


def build_user_message(
    packet: ExplanationPacket,
    *,
    repair_errors: list[str] | None = None,
) -> str:
    facts = {
        "revision": packet.revision.model_dump(),
        "pull_request_body_status": "unverified author narrative",
    }
    graph = {
        "claims": [claim.model_dump() for claim in packet.claims],
        "symbols": [symbol.model_dump() for symbol in packet.symbols],
        "relationships": [item.model_dump() for item in packet.relationships],
    }
    blocks = {
        "REPOSITORY FACTS": facts,
        "CHANGE GRAPH": graph,
        "EVIDENCE": [item.model_dump() for item in packet.evidence],
        "IMPACT ANALYSIS": [item.model_dump() for item in packet.impact],
        "UNKNOWN INFORMATION": {
            "unknowns": [item.model_dump() for item in packet.unknowns],
            "context_notes": [item.model_dump() for item in packet.context_notes],
            "tests": [item.model_dump() for item in packet.tests],
        },
        "BEHAVIOR FACTS": [item.model_dump() for item in packet.behavior_facts],
        "IMPACT FACTS": [item.model_dump() for item in packet.impact_facts],
        "TASK": depth_task(packet.depth),
        "OUTPUT SCHEMA": ExplanationDocument.model_json_schema(),
    }
    parts = [f"{name}\n{json.dumps(blocks[name], indent=2)}" for name in _BLOCKS]
    if repair_errors:
        parts.append("VALIDATOR ERRORS\n" + json.dumps(repair_errors, indent=2))
    return "\n\n".join(parts)
