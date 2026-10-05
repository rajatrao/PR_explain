"""Impact facts: what a pull request touches at each level, as grounded sentences.

Levels and their sources (all stored by analysis):

* ``system``     entry points that reach changed behavior (behavior facts), files
                 outside the diff that reach changed code (``reaches_changed``),
                 files no path reaches (``behavior_unchanged``), contract changes on
                 exported functions, and partial-view notes,
* ``api``        HTTP routes added, removed, or changed (``surface_changed``),
* ``data``       tables, columns, and model fields (``surface_changed``),
* ``config``     environment variables and settings (``surface_changed``),
* ``dependency`` packages (``surface_changed``, ``dependency_changed``),
* ``ui``         web interface files (``surface_changed``),
* ``testing``    stored test references for changed functions and changed test files.

Each fact lists the behavior facts (``b…`` ids) in the same files, so a reader
can tie a surface to the behavior that changed with it. File paths are not
written into the sentences.
"""

from __future__ import annotations

from app.analyzer.parse import is_test_path
from app.explanation.schema import BehaviorFunctionFact, ImpactFact

LEVELS = ("system", "api", "data", "config", "dependency", "ui", "testing")
LEVEL_LABEL = {
    "system": "System",
    "api": "API and contracts",
    "data": "Data",
    "config": "Configuration and operations",
    "dependency": "Dependencies",
    "ui": "User interface",
    "testing": "Testing",
}
NAME_CAP = 6


def build_impact_facts(*, claims, evidences, behavior_facts: list[BehaviorFunctionFact]) -> list[ImpactFact]:
    evidence_by_id = {_id(item): item for item in evidences or []}
    ids_by_file: dict[str, list[str]] = {}
    for fact in behavior_facts:
        ids_by_file.setdefault(fact.file, []).extend(change.id for change in fact.changes)

    facts: list[ImpactFact] = []

    def add(level: str, text: str, behavior_ids: list[str] | None = None) -> None:
        facts.append(ImpactFact(id=f"i{len(facts) + 1}", level=level, text=text, behavior_ids=_unique(behavior_ids or [])[:12]))

    # --- surfaces: api, data, config, dependency, ui ---------------------------------
    grouped: dict[str, list[tuple[str, str, str, str]]] = {}
    for claim in claims or []:
        if _kind(claim) != "surface_changed":
            continue
        for evidence_id in _evidence_ids(claim):
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None or not evidence.description:
                continue
            parts = evidence.description.split("|", 3)
            if len(parts) != 4:
                continue
            level, kind, name, detail = parts
            grouped.setdefault(level, []).append((kind, name, detail, evidence.file or ""))
    for level in ("api", "data", "config", "dependency"):
        for kind, name, detail, path in grouped.get(level, []):
            add(level, _surface_sentence(level, kind, name, detail), ids_by_file.get(path))
    ui = grouped.get("ui", [])
    if ui:
        counts = {kind: sum(1 for k, *_ in ui if k == kind) for kind in ("added", "changed", "removed")}
        bits = [f"{n} {kind}" for kind, n in counts.items() if n]
        ui_ids = [bid for _k, _n, _d, path in ui for bid in ids_by_file.get(path, [])]
        add("ui", f"The web interface changes: {', '.join(bits)} interface file{'s' if len(ui) != 1 else ''}.", ui_ids)
    if "dependency" not in grouped:
        for claim in claims or []:
            if _kind(claim) == "dependency_changed":
                add("dependency", _text(claim).replace("No call graph was inferred from the manifest.", "").strip())

    # --- system --------------------------------------------------------------------------
    entries = _unique([name for fact in behavior_facts for name in fact.reached_from])
    public_ids = [change.id for fact in behavior_facts if fact.public for change in fact.changes]
    if entries:
        add(
            "system",
            f"Changed behavior is reachable from {len(entries)} public entry point{'s' if len(entries) != 1 else ''}: "
            + ", ".join(entries[:NAME_CAP])
            + (f" and {len(entries) - NAME_CAP} more" if len(entries) > NAME_CAP else "")
            + ".",
            [change.id for fact in behavior_facts if fact.reached_from for change in fact.changes],
        )
    reaching = [claim for claim in claims or [] if _kind(claim) == "reaches_changed" and not is_test_path(_subject(claim))]
    if reaching:
        add(
            "system",
            f"{len(reaching)} production file{'s' if len(reaching) != 1 else ''} outside this diff reach the changed code through stored call or import paths, so their runtime behavior can change without being edited.",
            public_ids,
        )
    unchanged = sum(1 for claim in claims or [] if _kind(claim) == "behavior_unchanged")
    if unchanged:
        add("system", f"{unchanged} production file{'s have' if unchanged != 1 else ' has'} no stored call or import path into the changed code.")
    contract = [fact for fact in behavior_facts if fact.public and any(c.kind == "signature" for c in fact.changes)]
    if contract:
        add(
            "system",
            f"{len(contract)} public function{'s change their' if len(contract) != 1 else ' changes its'} parameters, so their callers see a different contract.",
            [c.id for fact in contract for c in fact.changes if c.kind == "signature"],
        )
        for fact in contract:
            for note in fact.notes:
                add("system", note, [c.id for c in fact.changes if c.kind == "signature"])
    removed = [fact for fact in behavior_facts if fact.removed]
    if removed:
        add(
            "system",
            f"{len(removed)} function{'s are' if len(removed) != 1 else ' is'} no longer defined at the head commit.",
            [c.id for fact in removed for c in fact.changes],
        )
    partial = sum(1 for claim in claims or [] if _kind(claim) in {"fanout_truncated", "ambiguous_call"})
    if partial:
        add("system", f"The caller view is partial: {partial} call site{'s were' if partial != 1 else ' was'} truncated or ambiguous.")

    # --- testing -------------------------------------------------------------------------
    changed_names = {_subject(claim) for claim in claims or [] if _kind(claim) == "symbol_changed"}
    tested = {_subject(claim) for claim in claims or [] if _kind(claim) == "tests" and _subject(claim) in changed_names}
    untested = {_subject(claim) for claim in claims or [] if _kind(claim) == "missing_test" and _subject(claim) in changed_names}
    if changed_names:
        add(
            "testing",
            f"{len(tested)} of {len(changed_names)} changed function{'s have' if len(changed_names) != 1 else ' has'} a stored test reference; {len(untested)} {'have' if len(untested) != 1 else 'has'} none.",
            [c.id for fact in behavior_facts if fact.public and not fact.tests for c in fact.changes][:12],
        )
    test_files = [_subject(claim) for claim in claims or [] if _kind(claim) == "file_changed" and is_test_path(_subject(claim))]
    if test_files:
        add("testing", f"{len(test_files)} test file{'s change' if len(test_files) != 1 else ' changes'} in this pull request.")
    return facts


def _surface_sentence(level: str, kind: str, name: str, detail: str) -> str:
    verb = {"added": "is added", "removed": "is removed", "changed": "changes"}.get(kind, kind)
    if level == "api":
        return f"HTTP route {name} {verb}."
    if level == "data":
        noun, _, ident = name.partition(" ")
        if "dropped" in detail:
            return f"The migration drops {noun} {ident}."
        return f"Database {noun} {ident} {verb} ({detail})."
    if level == "config":
        return f"{detail[:1].upper() + detail[1:]} {name} {verb}."
    if level == "dependency":
        return f"Package {name} {verb} ({detail})."
    return f"{name} {verb}."


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _kind(claim) -> str:
    return getattr(claim, "kind", "") or ""


def _subject(claim) -> str:
    return getattr(claim, "subject", None) or ""


def _text(claim) -> str:
    return getattr(claim, "text", None) or ""


def _evidence_ids(claim) -> list[str]:
    ids = getattr(claim, "evidence_public_ids", None)
    if ids is None:
        ids = getattr(claim, "evidence_ids", None)
    return list(ids or [])


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)
