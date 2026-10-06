"""System-level facts for the Explain tab: components, coupling, blast radius, and risks.

Everything here is computed from stored analysis facts (changed functions and their before/after
statements, CALLS edges at the head commit, call-site findings, review signals, surface findings),
then phrased for an architect: components (directories) rather than files, flows rather than
functions, and risks as plain statements. Each item keeps evidence links to the lines it rests on.
Nothing is inferred beyond what those facts state.
"""

from __future__ import annotations

import posixpath

from app.analyzer.behavior import call_names
from app.analyzer.parse import is_test_path
from app.explanation.schema import BehaviorFunctionFact

# Directory names that say nothing about the component.
_GENERIC_DIRS = {
    "src", "app", "apps", "lib", "libs", "backend", "frontend", "server", "client", "pkg", "internal",
    "source", "sources", "main", "java", "python", "packages", "modules", "code", "core",
}


def component(path: str | None) -> str:
    """backend/app/explanation/impact.py → "explanation"; src/session.ts → "session"."""
    if not path:
        return "unknown"
    dirs = [d for d in posixpath.dirname(path).split("/") if d and d.lower() not in _GENERIC_DIRS]
    if dirs:
        return dirs[-1]
    stem = posixpath.splitext(posixpath.basename(path))[0]
    return stem or path


def _private(name: str | None) -> bool:
    return (name or "").rsplit(".", 1)[-1].startswith(("_", "#"))


def _humanize(name: str) -> str:
    from app.explanation.plain_summary import humanize

    return humanize(name)


def _join(items: list[str], cap: int = 4) -> str:
    from app.explanation.plain_summary import _list

    return _list(items, cap)


def system_facts(*, symbols, relationships, claims, evidences, behavior_facts: list[BehaviorFunctionFact], repo, sha) -> dict:
    """Components changed, new and removed cross-component dependencies, blast radius, and risks."""
    from app.explanation.key_changes import outside_calls

    evidence_by_id = {getattr(e, "public_id", None) or getattr(e, "id", None): e for e in evidences or []}
    functions = [s for s in symbols or [] if getattr(s, "kind", None) == "function"]
    by_name: dict[str, list] = {}
    for symbol in functions:
        by_name.setdefault(symbol.name, []).append(symbol)

    public = [fact for fact in behavior_facts if fact.public and not is_test_path(fact.file)]
    changed_components = sorted({component(fact.file) for fact in public})

    # Cross-component dependencies added or dropped by changed call statements.
    added_deps: dict[tuple[str, str], list[dict]] = {}
    dropped_deps: dict[tuple[str, str], list[dict]] = {}
    for fact in public:
        source = component(fact.file)
        for change in fact.changes:
            if change.kind != "call" or bool(change.before) == bool(change.after):
                continue
            text = change.after or change.before or ""
            for name in call_names(text):
                tail = name.rsplit(".", 1)[-1]
                targets = by_name.get(tail, [])
                if len(targets) != 1 or _private(tail):
                    continue
                target = component(targets[0].file_path)
                if target == source:
                    continue
                bucket = added_deps if change.after else dropped_deps
                bucket.setdefault((source, target), []).append(_link(change.location, change.href))

    # Blast radius: entry points that reach changed code, and call sites outside this diff.
    entries = sorted({name for fact in public for name in fact.reached_from})
    entry_components = sorted(
        {component(by_name[name][0].file_path) for name in entries if by_name.get(name)}
    )
    calls, indirect = outside_calls(
        symbols=symbols, relationships=relationships, claims=claims, evidences=evidences, repo=repo, sha=sha
    )
    calls = [call for call in calls if not _private(call["callee"])]
    outside_components = sorted({component(call["file"]) for call in calls})

    risks: list[dict] = []

    def risk(severity: str, text: str, evidence: list[dict], title: str = "") -> None:
        risks.append({"severity": severity, "title": title, "text": text, "evidence": [e for e in evidence if e][:4]})

    missing = [c for c in calls if c["flags"].get("missing")]
    if missing:
        risk(
            "high",
            f"{_count(len(missing), 'call site')} outside this change {'does' if len(missing) == 1 else 'do'} not supply a newly required input "
            f"(in {_join(sorted({component(c['file']) for c in missing}))}); those requests will not match the new contract.",
            [_link(f"{c['file']}:{c['line']}" if c["line"] else c["file"], c["href"]) for c in missing],
            title="Callers outside the change break the new contract",
        )
    dangling = [c for c in claims or [] if getattr(c, "kind", None) == "dangling_call" and not _private(getattr(c, "subject", ""))]
    if dangling:
        places = [_claim_link(c, evidence_by_id, repo) for c in dangling]
        risk(
            "high",
            f"A capability this change removes is still called from {len(dangling)} place{'s' if len(dangling) != 1 else ''}"
            f" (in {_join(sorted({component(p['label']) for p in places if p}))}).",
            places,
            title="Removed capability still in use",
        )
    unhandled = [c for c in calls if c["flags"].get("error") and c["flags"].get("handled") is False]
    if unhandled:
        risk(
            "high",
            f"A new or changed failure reaches {_count(len(unhandled), 'caller')} that {'does' if len(unhandled) == 1 else 'do'} not handle it "
            f"(in {_join(sorted({component(c['file']) for c in unhandled}))}).",
            [_link(f"{c['file']}:{c['line']}" if c["line"] else c["file"], c["href"]) for c in unhandled],
            title="Unhandled new failure",
        )
    defaults = [c for c in calls if c["flags"].get("defaults")]
    if defaults:
        risk(
            "medium",
            f"{_count(len(defaults), 'call site')} outside this change {'relies' if len(defaults) == 1 else 'rely'} on a default value this change alters, "
            f"so {'its' if len(defaults) == 1 else 'their'} behavior changes silently.",
            [_link(f"{c['file']}:{c['line']}" if c["line"] else c["file"], c["href"]) for c in defaults],
            title="Changed default affects existing callers",
        )
    stale = [c for c in claims or [] if getattr(c, "kind", None) == "stale_test" and not _private(getattr(c, "subject", ""))]
    if stale:
        risk(
            "medium",
            f"{_count(len(stale), 'test')} {'exercises' if len(stale) == 1 else 'exercise'} changed behavior but {'was' if len(stale) == 1 else 'were'} not updated, "
            "so it may still assert the old behavior.",
            [_claim_link(c, evidence_by_id, repo) for c in stale],
            title="Tests not updated",
        )
    undocumented = [c for c in claims or [] if getattr(c, "kind", None) == "config_undocumented"]
    if undocumented:
        risk(
            "medium",
            f"{_count(len(undocumented), 'new configuration setting')} {'is' if len(undocumented) == 1 else 'are'} read but not documented for deployment; "
            "environments without them may fail or fall back silently.",
            [_claim_link(c, evidence_by_id, repo) for c in undocumented],
            title="Undocumented configuration",
        )
    live = [fact for fact in public if not fact.removed]
    untested = [fact for fact in live if not fact.tests and (fact.reached_from or fact.callers_at_head)]
    if untested:
        risk(
            "medium",
            (
                "No test exercises the changed behavior."
                if len(untested) == len(live)
                else f"No test exercises the changed behavior on {len(untested)} of {len(live)} changed public steps."
            ),
            [_link(c.location, c.href) for fact in untested for c in fact.changes[:1]],
            title="Untested behavior change",
        )

    return {
        "changed_components": changed_components,
        "added_deps": [{"from": a, "to": b, "evidence": ev[:3]} for (a, b), ev in sorted(added_deps.items())],
        "dropped_deps": [{"from": a, "to": b, "evidence": ev[:3]} for (a, b), ev in sorted(dropped_deps.items())],
        "entries": entries,
        "entry_components": entry_components,
        "outside_calls": len(calls),
        "outside_components": outside_components,
        "indirect_files": len(indirect),
        "risks": sorted(risks, key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["severity"]]),
    }


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def _link(location: str | None, href: str | None) -> dict | None:
    return {"label": location, "href": href} if location else None


def _claim_link(claim, evidence_by_id, repo) -> dict | None:
    for evidence_id in getattr(claim, "evidence_public_ids", None) or getattr(claim, "evidence_ids", None) or []:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or not getattr(evidence, "file", None):
            continue
        line = getattr(evidence, "start_line", None)
        href = (
            f"https://github.com/{repo}/blob/{evidence.commit_sha}/{evidence.file}" + (f"#L{line}" if line else "")
            if repo and getattr(evidence, "commit_sha", None)
            else None
        )
        return {"label": f"{evidence.file}:{line}" if line else evidence.file, "href": href}
    return None


# --- sentences ---------------------------------------------------------------------------------


def coupling_sentences(system: dict) -> list[str]:
    out = []
    for dep in system.get("added_deps") or []:
        out.append(f"{dep['from']} now depends on {dep['to']}")
    for dep in system.get("dropped_deps") or []:
        out.append(f"{dep['from']} no longer depends on {dep['to']}")
    return out


def blast_radius_sentence(system: dict) -> str:
    parts: list[str] = []
    entries = system.get("entries") or []
    if entries:
        where = f" in {_join(system['entry_components'])}" if system.get("entry_components") else ""
        parts.append(f"{len(entries)} entry point{'s' if len(entries) != 1 else ''}{where} reach the changed code")
    if system.get("outside_calls"):
        n = system["outside_calls"]
        parts.append(
            f"{n} call site{'s' if n != 1 else ''} outside this change depend on it"
            + (f" (in {_join(system['outside_components'])})" if system.get("outside_components") else "")
        )
    if system.get("indirect_files"):
        n = system["indirect_files"]
        parts.append(f"{n} more file{'s' if n != 1 else ''} reach it indirectly")
    if not parts:
        return "No stored call path from outside this change reaches the changed code."
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."
