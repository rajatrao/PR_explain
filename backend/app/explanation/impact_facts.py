"""Impact facts: the areas a pull request touches and the reviewer risks they carry.

Area facts (kind "area") say what is touched, in terms a reviewer recognises:

* Public API      HTTP routes added, removed, or changed,
* Data            tables, columns, and model fields,
* Configuration   environment variables and settings,
* Dependencies    packages and versions,
* Web interface   interface files,
* Affected flows  public entry points that reach changed behavior.

Risk facts (kind "risk") are derived from the same findings with a fixed
severity, so the severity is never the model's guess:

* high    a route is removed, a table or column is dropped, or a head call site
          passes a different number of arguments than a changed public signature,
* medium  a new setting must be provided, a package crosses a major version,
          a public function changes what callers must pass or is removed, a
          schema change needs a migration, a route's declaration changes,
* low     a package is added or removed, a setting is no longer read, an
          example value changes.

Sources: ``surface_changed`` claims (analysis of the diff lines) and behavior
facts (entry points, signature changes, contract notes). Analyzer bookkeeping such
as file counts, unreached files, and truncated call sites is not impact and is
left out, and so is test coverage.
"""

from __future__ import annotations

import re

from app.explanation.behavior_facts import param_delta
from app.explanation.schema import BehaviorFunctionFact, ImpactFact

AREAS = ("Affected flows", "Public API", "Data", "Configuration", "Dependencies", "Web interface")
SEVERITIES = ("high", "medium", "low")
NAME_CAP = 6


def build_impact_facts(*, claims, evidences, behavior_facts: list[BehaviorFunctionFact]) -> list[ImpactFact]:
    evidence_by_id = {_id(item): item for item in evidences or []}
    ids_by_file: dict[str, list[str]] = {}
    for fact in behavior_facts:
        ids_by_file.setdefault(fact.file, []).extend(change.id for change in fact.changes)

    facts: list[ImpactFact] = []

    def add(kind: str, area: str, text: str, *, severity: str | None = None, behavior_ids=None) -> None:
        item = ImpactFact(
            id=f"i{len(facts) + 1}",
            kind=kind,
            area=area,
            text=text,
            severity=severity,
            behavior_ids=_unique(list(behavior_ids or []))[:12],
        )
        if not any(existing.text == text for existing in facts):
            facts.append(item)

    # --- affected flows ---------------------------------------------------------------
    entries = _unique([name for fact in behavior_facts for name in fact.reached_from])
    if entries:
        shown = ", ".join(entries[:NAME_CAP]) + (f" and {len(entries) - NAME_CAP} more" if len(entries) > NAME_CAP else "")
        add(
            "area",
            "Affected flows",
            f"Changed behavior is reachable from public entry point{'s' if len(entries) != 1 else ''} {shown}.",
            behavior_ids=[c.id for fact in behavior_facts if fact.reached_from for c in fact.changes],
        )
    for fact in behavior_facts:
        if not fact.public:
            continue
        signature = next((c for c in fact.changes if c.kind == "signature" and c.before and c.after), None)
        if signature is not None:
            added, removed, defaults = param_delta(signature.before, signature.after, fact.file)
            required = [name for name, optional in added if not optional]
            parts = []
            if required:
                parts.append("must now pass " + ", ".join(required))
            if removed:
                parts.append("no longer pass " + ", ".join(removed))
            for name, old, new in defaults:
                parts.append(f"get {new} instead of {old} when they omit {name}")
            if parts:
                add("risk", "Affected flows", f"Callers of {fact.function} " + "; ".join(parts) + ".", severity="medium", behavior_ids=[signature.id])
        for note in fact.notes:
            if " passes " in note:
                add("risk", "Affected flows", f"A caller may break: {note}", severity="high", behavior_ids=[c.id for c in fact.changes if c.kind == "signature"])
        if fact.removed:
            add("risk", "Affected flows", f"{fact.function} is no longer available to callers.", severity="medium", behavior_ids=[c.id for c in fact.changes])

    # --- surfaces -----------------------------------------------------------------------
    ui_files: list[tuple[str, str]] = []
    for claim in claims or []:
        if _kind(claim) != "surface_changed":
            continue
        for evidence_id in _evidence_ids(claim):
            evidence = evidence_by_id.get(evidence_id)
            parts = (evidence.description or "").split("|", 3) if evidence is not None else []
            if len(parts) != 4:
                continue
            level, change, name, detail = parts
            related = ids_by_file.get(evidence.file or "", [])
            if level == "ui":
                ui_files.append((change, evidence.file or ""))
                continue
            _surface(add, level, change, name, detail, related)
    if ui_files:
        counts = {change: sum(1 for c, _ in ui_files if c == change) for change in ("added", "changed", "removed")}
        bits = [f"{n} {change}" for change, n in counts.items() if n]
        related = [bid for _c, path in ui_files for bid in ids_by_file.get(path, [])]
        add("area", "Web interface", f"The web interface changes ({', '.join(bits)} interface file{'s' if len(ui_files) != 1 else ''}).", behavior_ids=related)

    if not any(fact.area == "Dependencies" for fact in facts):
        for claim in claims or []:
            if _kind(claim) == "dependency_changed":
                add("area", "Dependencies", _text(claim).replace("No call graph was inferred from the manifest.", "").strip())
    return facts


def _surface(add, level: str, change: str, name: str, detail: str, related: list[str]) -> None:
    verb = {"added": "is added", "removed": "is removed", "changed": "changes"}.get(change, change)
    if level == "api":
        add("area", "Public API", f"Route {name} {verb}.", behavior_ids=related)
        if change == "removed":
            add("risk", "Public API", f"Route {name} is removed; clients that call it will no longer reach it.", severity="high", behavior_ids=related)
        elif change == "changed":
            add("risk", "Public API", f"The declaration of route {name} changes; check that existing clients still match it.", severity="medium", behavior_ids=related)
        return
    if level == "data":
        noun, _, ident = name.partition(" ")
        if "dropped" in detail:
            add("area", "Data", f"The {noun} {ident} is dropped.", behavior_ids=related)
            add("risk", "Data", f"A migration drops the {noun} {ident}; data stored in it is lost when the migration runs.", severity="high", behavior_ids=related)
        elif detail.startswith("ORM"):
            add("area", "Data", f"The stored {noun} {ident} {verb} in the data model.", behavior_ids=related)
        else:
            add("area", "Data", f"The database {noun} {ident} {verb}.", behavior_ids=related)
            if change == "added":
                add("risk", "Data", f"The schema change for {noun} {ident} needs its migration applied wherever this is deployed.", severity="medium", behavior_ids=related)
        return
    if level == "config":
        add("area", "Configuration", f"The {detail} {name} {verb}.", behavior_ids=related)
        if change == "added" and "example" not in detail:
            add("risk", "Configuration", f"New setting {name}: every environment must provide it or rely on a default.", severity="medium", behavior_ids=related)
        elif change == "removed" and "read by the code" in detail:
            add("risk", "Configuration", f"Setting {name} is no longer read here; environments that still set it can drop it.", severity="low", behavior_ids=related)
        return
    if level == "dependency":
        if change == "changed" and "→" in detail:
            old, new = (part.strip() for part in detail.split("→", 1))
            add("area", "Dependencies", f"Package {name} moves from {old} to {new}.", behavior_ids=related)
            if _major(old) is not None and _major(new) is not None and _major(old) != _major(new):
                add("risk", "Dependencies", f"Package {name} crosses a major version ({old} to {new}).", severity="medium", behavior_ids=related)
        elif change == "added":
            add("area", "Dependencies", f"Package {name} ({detail}) is added.", behavior_ids=related)
            add("risk", "Dependencies", f"New package {name} becomes part of the build and runtime.", severity="low", behavior_ids=related)
        elif change == "removed":
            add("area", "Dependencies", f"Package {name} is removed.", behavior_ids=related)
            add("risk", "Dependencies", f"Package {name} is removed; anything that still imports it will fail.", severity="low", behavior_ids=related)
        else:
            add("area", "Dependencies", f"Package {name} changes ({detail}).", behavior_ids=related)


def _major(version: str) -> int | None:
    match = re.search(r"\d+", version or "")
    return int(match.group()) if match else None


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _text(claim) -> str:
    return getattr(claim, "text", None) or ""


def _evidence_ids(claim) -> list[str]:
    ids = getattr(claim, "evidence_public_ids", None)
    if ids is None:
        ids = getattr(claim, "evidence_ids", None)
    return list(ids or [])


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)
