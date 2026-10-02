from __future__ import annotations

from app.analyzer.types import Claim


def claim_key(claim: Claim) -> str:
    text = " ".join(claim.text.split())
    return "|".join([claim.kind, claim.subject or "", claim.epistemic, text])


def compare_claim_sets(previous: list[Claim], current: list[Claim]) -> dict:
    old = {claim_key(claim): claim for claim in previous}
    new = {claim_key(claim): claim for claim in current}
    added = [_public_claim(new[key]) for key in sorted(set(new) - set(old))]
    removed = [_public_claim(old[key]) for key in sorted(set(old) - set(new))]
    return {
        "added": added,
        "removed": removed,
        "unchanged_count": len(set(old) & set(new)),
    }


def _public_claim(claim: Claim) -> dict:
    return {
        "key": claim_key(claim),
        "id": claim.id,
        "epistemic": claim.epistemic,
        "kind": claim.kind,
        "text": claim.text,
        "subject": claim.subject,
    }
