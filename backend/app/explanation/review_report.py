"""The Review tab: where a reviewer should spend limited time on this pull request.

Two sources feed one ``ReviewReport``:

* Rules (``rule_review``): findings the analyzer can state from stored facts. A call outside
  the diff that does not pass a newly required argument, a changed failure that reaches a
  caller with no try block, a changed default a caller relies on, a test that exercises
  changed code and was not updated, a newly read config value no env example lists, a changed
  function no test references, a changed route or data model. These are always available.

* The model (``screen_review``): a senior-reviewer pass over the diff text and the same facts
  (prompt ``prompts/review.md``). Every item it writes must cite fact ids (b…, i…, r…) or diff
  locations ("path:line") that exist; code it names must appear in the diff or the facts. Items
  that fail are dropped, never repaired. A "confirmed" bug must rest on a rule fact.

``merge_review`` keeps the model's items and adds rule items the model did not cover, so a
missing or rejected model reply still leaves a useful, grounded report.
"""

from __future__ import annotations

import json
import re

from app.analyzer.parse import is_test_path
from app.explanation.behavioral_changes import _identifiers
from app.explanation.key_changes import _first_sentence, _join, _lead, outside_calls
from app.explanation.schema import (
    AttentionArea,
    MissingTest,
    PotentialBug,
    ReviewerQuestion,
    ReviewFact,
    ReviewReport,
    RiskDriver,
    SafeArea,
    TopQuestion,
)

DIFF_BUDGET = 24000
FILE_BUDGET = 6000
CAPS = {"attention": 8, "questions": 15, "bugs": 8, "missing_tests": 12, "safe": 6, "top_questions": 10}
_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
_SEVERITY_TO_PRIORITY = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
_GENERIC = re.compile(
    r"^(is (this|it) (tested|secure|safe|correct)|could this be (optimi[sz]ed|faster|improved)|are there (any )?tests)\??$",
    re.IGNORECASE,
)
_LOCATION = re.compile(r"^(?P<path>[^\s:]+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$")
_SKIP_DIFF = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Cargo\.lock|go\.sum)$|(^|/)(dist|build|vendor)/")


# --- facts ----------------------------------------------------------------------------------


def build_review_facts(*, symbols, relationships, claims, evidences, repo, sha) -> list[ReviewFact]:
    """Rule findings the review may cite (r1, r2, …), most severe first."""
    evidence_by_id = {_id(e): e for e in evidences or []}
    calls, _ = outside_calls(
        symbols=symbols, relationships=relationships, claims=claims, evidences=evidences, repo=repo, sha=sha
    )
    found: list[tuple[str, str, str | None, str]] = []  # kind, text, location, severity
    for call in calls:
        where = f"{call['file']}:{call['line']}" if call["line"] else call["file"]
        shown = f"`{call['call']}`" if call["call"] else call["callee"]
        text = f"{call['caller'] or 'Code'} in {where} calls {shown}, which this pull request changes. " + " ".join(call["notes"])
        flags = call["flags"]
        severity = "low"
        if flags.get("missing"):
            severity = "high"
        elif flags.get("error") and flags.get("handled") is False or flags.get("removed") or flags.get("defaults"):
            severity = "medium"
        found.append(("caller", text.strip(), where, severity))
    changed = {getattr(c, "subject", None) for c in claims or [] if getattr(c, "kind", None) == "behavior_changed"}
    for claim in claims or []:
        kind = getattr(claim, "kind", None)
        text = (getattr(claim, "text", None) or "").strip()
        subject = getattr(claim, "subject", None) or ""
        where = _claim_location(claim, evidence_by_id)
        if kind == "call_context" and "without" in text and subject in changed:
            found.append(("error_handling", text, where, "medium"))
        elif kind == "dangling_call":
            found.append(("dangling", text, where, "high"))
        elif kind == "stale_test":
            found.append(("stale_test", text, where, "medium"))
        elif kind == "config_undocumented":
            found.append(("config", text, where, "medium"))
        elif kind == "missing_test" and subject in changed:
            found.append(("untested", text, where, "medium"))
        elif kind == "surface_changed":
            level = subject.split(":", 1)[0]
            severity = "high" if level in {"route", "data"} else "medium" if level in {"config", "dependency"} else "low"
            found.append(("surface", text, where, severity))
        elif kind == "resolution_coverage":
            found.append(("coverage", text, None, "low"))
    order = {"high": 0, "medium": 1, "low": 2}
    found.sort(key=lambda item: order.get(item[3], 3))
    return [
        ReviewFact(id=f"r{index}", kind=kind, text=text, location=where, severity=severity)
        for index, (kind, text, where, severity) in enumerate(found, start=1)
    ]


def diff_block(patches: dict[str, str] | None, *, budget: int = DIFF_BUDGET) -> tuple[str, dict[str, set[int]]]:
    """The diff as text with line numbers, production files first, and the lines it shows per file.

    Added and context lines carry their head line number; removed lines carry "old N".
    """
    if not patches:
        return "", {}
    paths = [path for path in patches if not _SKIP_DIFF.search(path)]
    paths.sort(key=lambda path: (is_test_path(path), path))
    parts: list[str] = []
    lines_by_file: dict[str, set[int]] = {}
    used = 0
    for path in paths:
        body, lines = _numbered(patches[path] or "")
        if len(body) > FILE_BUDGET:
            body = body[:FILE_BUDGET].rsplit("\n", 1)[0] + "\n… (rest of this file's diff not shown)"
        block = f"--- {path}\n{body}"
        if used + len(block) > budget:
            parts.append(f"--- {path}\n(not shown: diff budget reached)")
            continue
        parts.append(block)
        lines_by_file[path] = lines
        used += len(block)
    return "\n\n".join(parts), lines_by_file


def _numbered(patch: str) -> tuple[str, set[int]]:
    out: list[str] = []
    lines: set[int] = set()
    old = new = 0
    for raw in patch.splitlines():
        hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if hunk:
            old, new = int(hunk.group(1)), int(hunk.group(2))
            out.append(raw)
            continue
        if raw.startswith("-"):
            out.append(f"old {old:>4} - {raw[1:]}")
            lines.add(old)
            old += 1
        elif raw.startswith("+"):
            out.append(f"{new:>8} + {raw[1:]}")
            lines.add(new)
            new += 1
        elif raw.startswith("\\"):
            continue
        else:
            out.append(f"{new:>8}   {raw[1:] if raw.startswith(' ') else raw}")
            lines.add(new)
            old += 1
            new += 1
    return "\n".join(out), lines


# --- model request ----------------------------------------------------------------------------


def review_user_message(*, task: str, diff: str, behavior_facts, impact_facts, review_facts, pr_title: str | None) -> str:
    blocks = [
        ("PULL REQUEST", {"title": pr_title or "", "body_status": "the description is unverified author narrative"}),
        ("DIFF", diff or "(the diff text is not available; use the facts only)"),
        ("BEHAVIOR FACTS", [item.model_dump() for item in behavior_facts or []]),
        ("IMPACT FACTS", [item.model_dump() for item in impact_facts or []]),
        ("REVIEW FACTS", [item.model_dump() for item in review_facts or []]),
        ("TASK", task),
        ("OUTPUT SCHEMA", ReviewReport.model_json_schema()),
    ]
    return "\n\n".join(f"{name}\n{value if isinstance(value, str) else json.dumps(value, indent=2)}" for name, value in blocks)


# --- screening --------------------------------------------------------------------------------


class _Ground:
    def __init__(self, *, diff: str, lines_by_file: dict[str, set[int]], behavior_facts, impact_facts, review_facts) -> None:
        self.ids: dict[str, str] = {}
        for fact in behavior_facts or []:
            for change in fact.changes:
                self.ids[change.id] = "behavior"
        for fact in impact_facts or []:
            self.ids[fact.id] = "impact"
        self.rule_level: dict[str, str] = {}
        for fact in review_facts or []:
            self.ids[fact.id] = "rule"
            self.rule_level[fact.id] = _SEVERITY_TO_PRIORITY.get(fact.severity or "low", "Low")
        self.lines_by_file = lines_by_file
        self.fact_files = {
            (fact.location or "").split(":", 1)[0] for fact in review_facts or [] if fact.location
        } | {fact.file for fact in behavior_facts or [] if fact.file}
        self.raw = "\n".join(
            [
                diff,
                json.dumps([f.model_dump() for f in behavior_facts or []]),
                json.dumps([f.model_dump() for f in impact_facts or []]),
                json.dumps([f.model_dump() for f in review_facts or []]),
            ]
        )
        self.corpus = _normalize(
            "\n".join(
                [
                    diff,
                    json.dumps([f.model_dump() for f in behavior_facts or []]),
                    json.dumps([f.model_dump() for f in impact_facts or []]),
                    json.dumps([f.model_dump() for f in review_facts or []]),
                ]
            )
        )

    def location(self, text: str) -> str | None:
        match = _LOCATION.match((text or "").strip().strip("`"))
        if not match:
            return None
        path = match.group("path")
        if path not in self.lines_by_file and path not in self.fact_files:
            return None
        start = match.group("start")
        if start is None:
            return path
        shown = self.lines_by_file.get(path)
        if shown is not None and shown and not any(abs(int(start) - line) <= 3 for line in shown):
            return None
        return text.strip().strip("`")

    def unknown_code(self, text: str) -> str | None:
        for token in _identifiers(text or ""):
            if _normalize(token) not in self.corpus:
                return token
        return None


def screen_review(raw: dict | None, ground: _Ground, reasons: list[str] | None = None) -> ReviewReport | None:
    log = reasons if reasons is not None else []
    if not isinstance(raw, dict):
        log.append("the model returned no review")
        return None
    report = ReviewReport()
    sections = {
        "attention": (AttentionArea, ("area", "why_it_matters", "what_changed", "what_could_go_wrong")),
        "questions": (ReviewerQuestion, ("question",)),
        "bugs": (PotentialBug, ("finding", "evidence", "scenario", "impact")),
        "missing_tests": (MissingTest, ("scenario", "verifies")),
        "safe": (SafeArea, ("area", "why")),
        "top_questions": (TopQuestion, ("question", "why_ask", "relevant_code")),
    }
    for field, (model, texts) in sections.items():
        kept = []
        for index, item in enumerate(raw.get(field) or []):
            try:
                parsed = model.model_validate({**item, "source": "model"} if isinstance(item, dict) else item)
            except Exception:
                log.append(f"{field}[{index}]: did not match the schema")
                continue
            issue = _item_problem(parsed, texts, ground)
            if issue:
                log.append(f"{field}[{index}]: {issue}")
                continue
            if isinstance(parsed, AttentionArea):
                parsed.priority = _capped_priority(parsed, ground)
            if isinstance(parsed, PotentialBug) and parsed.status == "confirmed":
                if parsed.confidence != "High" or not any(ground.ids.get(i) == "rule" for i in parsed.fact_ids):
                    parsed.status = "possible"
            kept.append(parsed)
            if len(kept) >= CAPS[field]:
                break
        setattr(report, field, kept)
    report.undetermined = [
        text.strip() for text in raw.get("undetermined") or [] if isinstance(text, str) and text.strip() and not ground.unknown_code(text)
    ][:6]
    risk = raw.get("overall_risk")
    report.overall_risk = risk if risk in _RANK else "Low"
    reason = (raw.get("risk_reason") or "").strip() if isinstance(raw.get("risk_reason"), str) else ""
    report.risk_reason = "" if ground.unknown_code(reason) else reason
    if not any(getattr(report, field) for field in sections):
        log.append("no review item passed the grounding check")
        return None
    return report


def _item_problem(item, texts: tuple[str, ...], ground: _Ground) -> str | None:
    item.fact_ids = [fact_id for fact_id in item.fact_ids if fact_id in ground.ids]
    item.locations = [loc for loc in (ground.location(text) for text in item.locations) if loc]
    if not item.fact_ids and not item.locations:
        return "cites no known fact or diff location"
    for name in texts:
        value = getattr(item, name, "") or ""
        if not value.strip():
            return f"{name} is empty"
        token = ground.unknown_code(value)
        if token:
            return f"{name} uses {token}, which is not in the diff or the facts"
    if isinstance(item, AttentionArea):
        item.involved = [name for name in item.involved if not ground.unknown_code(f"`{name}`")]
    question = getattr(item, "question", None)
    if question is not None:
        if not question.rstrip().endswith("?"):
            return "question is not a question"
        if _GENERIC.match(question.strip()):
            return "question is generic"
    return None


# --- rules ------------------------------------------------------------------------------------


def rule_review(
    *, symbols, relationships, claims, evidences, repo, sha, review_facts: list[ReviewFact], diff_available: bool
) -> ReviewReport:
    """The review the stored facts support on their own."""
    from app.explanation.behavior_comparison import build_behavior_comparison

    comparison = build_behavior_comparison(
        symbols=symbols or [], relationships=relationships or [], claims=claims or [], evidences=evidences or []
    )
    items = [item for item in comparison.get("items") or [] if item.get("changes")]
    calls, _ = outside_calls(
        symbols=symbols, relationships=relationships, claims=claims, evidences=evidences, repo=repo, sha=sha
    )
    caller_fact = {}
    for call in calls:
        for fact in review_facts:
            if fact.kind == "caller" and fact.location == (f"{call['file']}:{call['line']}" if call["line"] else call["file"]):
                caller_fact[id(call)] = fact
                break

    report = ReviewReport()
    # Attention: the changed functions callers depend on, most caller-visible first.
    for item in _ranked(items)[: CAPS["attention"]]:
        mine = [call for call in calls if call["callee"] == item["name"]]
        ids = [caller_fact[id(call)].id for call in mine if id(call) in caller_fact]
        location = item.get("location") or item.get("file")
        priority = "Low"
        risks: list[str] = []
        if any(call["flags"].get("missing") for call in mine):
            priority = "High"
            risks.append("Callers outside this diff that do not pass the new required input fail at the call.")
        if any(call["flags"].get("error") and call["flags"].get("handled") is False for call in mine):
            priority = _max(priority, "Medium")
            risks.append("The changed failure reaches callers that do not catch it.")
        if any(call["flags"].get("defaults") for call in mine):
            priority = _max(priority, "Medium")
            risks.append("Callers that rely on a changed default now get a different value.")
        dangling = [
            c for c in claims or [] if getattr(c, "kind", None) == "dangling_call" and getattr(c, "subject", None) == item["name"]
        ]
        if item.get("removed") and dangling:
            priority = _max(priority, "High")
            risks.append(
                f"{len(dangling)} call{'s' if len(dangling) != 1 else ''} at the head commit still use it and no longer "
                "resolve to a definition."
            )
        if item.get("removed"):
            if item.get("exported"):
                priority = _max(priority, "Medium")
            risks.append(
                "Code outside the analyzed files (other services, scripts, dynamic lookups) that still refers to it stops working."
            )
        tests = (item.get("reach") or {}).get("tests") or []
        if not tests and not item.get("removed"):
            priority = _max(priority, "Medium" if mine or item.get("exported") else "Low")
            risks.append("No test references this function, so a regression here would not be caught by the suite.")
        if not risks:
            risks.append("Callers receive the changed behavior; check they handle every new outcome.")
        if mine:
            why = f"{len(mine)} call{'s' if len(mine) != 1 else ''} from outside this diff depend on it."
        elif item.get("removed"):
            why = "No stored call still refers to it."
        elif item.get("exported"):
            why = "It is exported and part of the module's public surface."
        else:
            why = "Internal to the changed code."
        report.attention.append(
            AttentionArea(
                area=f"{item['name']} ({item.get('file')})",
                why_it_matters=why,
                what_changed="Removed by this pull request."
                if item.get("removed")
                else " ".join(_first_sentence(c["summary"]) for c in item["changes"][:2] if c.get("summary"))
                or "Statement-level changes.",
                what_could_go_wrong=" ".join(risks),
                involved=[location, *[f"{c['file']}:{c['line']}" if c["line"] else c["file"] for c in mine[:4]]],
                priority=priority,
                fact_ids=ids,
                locations=[location] if location else [],
                source="rules",
            )
        )
    for fact in review_facts:
        if fact.kind == "surface" and len(report.attention) < CAPS["attention"]:
            report.attention.append(
                AttentionArea(
                    area=fact.text.split(".")[0][:80],
                    why_it_matters="It changes a route, stored data, configuration, dependency, or UI surface others rely on.",
                    what_changed=fact.text,
                    what_could_go_wrong="Clients, stored records, or deployments built against the old surface may not match the new one.",
                    involved=[fact.location] if fact.location else [],
                    priority=_SEVERITY_TO_PRIORITY.get(fact.severity or "low", "Low"),
                    fact_ids=[fact.id],
                    source="rules",
                )
            )
    report.attention.sort(key=lambda area: _RANK[area.priority])

    # Questions and bugs from each caller finding and signal.
    questions: list[tuple[int, TopQuestion]] = []
    returns_by_callee: dict[str, list[tuple[dict, ReviewFact]]] = {}
    for call in calls:
        fact = caller_fact.get(id(call))
        if fact is None:
            continue
        flags, where = call["flags"], fact.location or call["file"]
        shown = f"`{call['call']}`" if call["call"] else call["callee"]
        if flags.get("missing"):
            missing = _join(flags["missing"])
            questions.append(
                (0, _top(
                    f"{call['caller'] or 'This caller'} calls {shown} without {missing}; how is this call updated now that {call['callee']} requires it?",
                    f"{call['callee']} now requires {missing} and this call, outside the diff, does not pass it.",
                    where, fact.id,
                ))
            )
            report.bugs.append(
                PotentialBug(
                    finding=f"A call outside the diff does not pass {missing}, which {call['callee']} now requires.",
                    evidence=fact.text,
                    scenario=f"{call['caller'] or 'The caller'} runs and reaches {shown} in {where}.",
                    impact="The call fails (a type error at build time or an argument error at run time), and the flow that depends on it stops.",
                    confidence="High",
                    status="confirmed",
                    fact_ids=[fact.id],
                    locations=[where],
                    source="rules",
                )
            )
        if flags.get("error") and flags.get("handled") is False:
            questions.append(
                (1, _top(
                    f"What happens in {call['caller'] or 'this caller'} when {call['callee']} takes its new failure path?",
                    f"{flags['error']} The call in {where} is not inside a try block.",
                    where, fact.id,
                ))
            )
            report.bugs.append(
                PotentialBug(
                    finding=f"The changed failure of {call['callee']} is not caught at {where}.",
                    evidence=fact.text,
                    scenario=f"{call['callee']} takes its failing branch while called from {call['caller'] or 'this caller'}.",
                    impact="The error propagates to whatever invoked the caller; whether that is handled further up is not known from the stored facts.",
                    confidence="Medium",
                    status="possible",
                    fact_ids=[fact.id],
                    locations=[where],
                    source="rules",
                )
            )
        for param, old, new in flags.get("defaults") or []:
            questions.append(
                (2, _top(
                    f"Is the new default of {param} ({old} → {new}) intended for {call['caller'] or 'the caller'} in {where}?",
                    f"The call does not pass {param}, so its behavior changes with the default and nothing in the diff shows that was checked.",
                    where, fact.id,
                ))
            )
        if flags.get("returns") and not flags.get("missing"):
            returns_by_callee.setdefault(call["callee"], []).append((call, fact))
        if flags.get("passed"):
            report.safe.append(
                SafeArea(
                    area=f"{call['caller'] or 'Caller'} in {where}",
                    why=f"Already passes {_join(flags['passed'])}, which {call['callee']} now requires.",
                    fact_ids=[fact.id],
                    locations=[where],
                    source="rules",
                )
            )
    for callee, pairs in returns_by_callee.items():
        item = pairs[0][0]["item"]
        change = next(c for c in item["changes"] if c["category"] == "return")
        if change.get("before") and change.get("after"):
            what = f"It returned {_code(_returned(change['before']))} and now returns {_code(_returned(change['after']))}"
        else:
            what = _first_sentence(change.get("summary") or "The returned value changed.").rstrip(".")
        when = f" when {change['after_when']}" if change.get("after_when") else ""
        names = _join([call["caller"] or call["file"] for call, _ in pairs[:4]]) + (f" and {len(pairs) - 4} more" if len(pairs) > 4 else "")
        questions.append(
            (3, TopQuestion(
                question=f"Do {names} handle the value {callee} now returns?" if len(pairs) > 1 else f"Does {names} handle the value {callee} now returns?",
                why_ask=f"{what}{when}. {'These callers are' if len(pairs) > 1 else 'This caller is'} outside the diff and {'were' if len(pairs) > 1 else 'was'} not updated.",
                relevant_code=", ".join(fact.location or "" for _, fact in pairs[:4]),
                fact_ids=[fact.id for _, fact in pairs],
                locations=[fact.location for _, fact in pairs[:4] if fact.location],
                source="rules",
            ))
        )
    for fact in review_facts:
        where = fact.location
        if fact.kind == "stale_test":
            questions.append((2, _top(
                f"Does {fact.text.split(' exercises ', 1)[0]} still assert the right outcome for the changed code?",
                fact.text, where or "", fact.id,
            )))
            report.missing_tests.append(
                MissingTest(group="Regression cases", scenario=fact.text, verifies="The test asserts the new behavior, not the old one.", fact_ids=[fact.id], source="rules")
            )
        elif fact.kind == "config":
            name = fact.text.split(" ", 1)[0]
            questions.append((2, _top(f"Where is {name} set in each environment, and what happens when it is missing?", fact.text, where or "", fact.id)))
        elif fact.kind == "surface" and fact.severity == "high":
            questions.append((1, _top(
                "Are existing clients and stored records compatible with this route or data change?", fact.text, where or "", fact.id
            )))

    # Missing tests: changed functions no test references, grouped by what changed.
    for item in _ranked(items):
        tests = (item.get("reach") or {}).get("tests") or []
        if tests:
            location = item.get("location") or item.get("file")
            report.safe.append(
                SafeArea(
                    area=item["name"],
                    why=f"Referenced by {_join(tests[:2])}; check that it covers the changed lines.",
                    locations=[location] if location else [],
                    fact_ids=[],
                    source="rules",
                )
            )
            continue
        if item.get("removed"):
            continue
        fact = next((f for f in review_facts if f.kind == "untested" and item["name"] in f.text), None)
        location = item.get("location") or item.get("file")
        for change in item["changes"]:
            group, verifies = _test_group(change)
            if group is None:
                continue
            report.missing_tests.append(
                MissingTest(
                    group=group,
                    scenario=f"{item['name']}: {_first_sentence(change.get('summary') or '')}",
                    verifies=verifies,
                    fact_ids=[fact.id] if fact else [],
                    locations=[location] if location else [],
                    source="rules",
                )
            )
        if fact:
            questions.append((2, _top(
                f"Which test covers the new behavior of {item['name']}?",
                f"{_lead(item)} {fact.text}", location or "", fact.id,
            )))
    report.missing_tests = _dedupe(report.missing_tests, lambda t: (t.group, t.scenario.split(":", 1)[0]))[: CAPS["missing_tests"]]

    questions.sort(key=lambda pair: pair[0])
    tops = _dedupe([question for _, question in questions], lambda q: q.question)
    report.top_questions = tops[: CAPS["top_questions"]]
    report.questions = [
        ReviewerQuestion(question=q.question, fact_ids=q.fact_ids, locations=q.locations, source="rules") for q in tops
    ][: CAPS["questions"]]
    report.bugs = report.bugs[: CAPS["bugs"]]
    report.safe = _dedupe(report.safe, lambda s: s.area)[: CAPS["safe"]]

    unchanged = sum(1 for c in claims or [] if getattr(c, "kind", None) == "behavior_unchanged")
    if unchanged and len(report.safe) < CAPS["safe"]:
        report.safe.append(
            SafeArea(
                area="Files outside the diff",
                why=f"{unchanged} file{'s' if unchanged != 1 else ''} outside the diff reach no changed function through a stored call or import.",
                fact_ids=[],
                locations=[],
                source="rules",
            )
        )
    for fact in review_facts:
        if fact.kind == "coverage":
            report.undetermined.append(f"{fact.text} Callers reached only through unresolved calls are not listed.")
    if not diff_available:
        report.undetermined.append("The diff text was not available, so this review rests on the stored facts only.")

    return assess_risk(report, review_facts)


def _test_group(change: dict) -> tuple[str | None, str]:
    category = change.get("category")
    when = change.get("after_when") or change.get("before_when")
    cond = f" when {when}" if when else ""
    if category == "error":
        return "Error/failure paths", f"The new failure{cond} is raised and surfaces the way callers expect."
    if category == "condition":
        return "Boundary cases", f"Inputs on both sides of the changed condition{cond} take the intended branch."
    if category == "signature":
        return "Regression cases", "Existing callers keep working with the new inputs, including defaults."
    if category == "return":
        return "Happy path", f"The returned value{cond} matches the new behavior."
    if category == "removed_function":
        return None, ""
    return None, ""


# --- overall risk -----------------------------------------------------------------------------

RISK_DRIVER_CAP = 4


def assess_risk(report: ReviewReport, review_facts: list[ReviewFact], *, model=None, ground=None, reasons=None) -> ReviewReport:
    """Set the overall risk from evidence, never from the model's say-so.

    * The floor is the most severe rule finding (r…): each is a stored fact with a location.
    * The model may raise the level by one step above that floor, and only when one of its own
      kept items (a potential bug, or an attention area at that priority) cites a rule fact or a
      diff line. Critical also needs a confirmed bug, which only a rule fact can confirm.
    * The reason is written from those findings. The model's own sentence is added only when it
      argued for the level the evidence supports, names nothing outside the diff and facts, and
      states no number, quoted value, or failure the evidence lacks.
    """
    log = reasons if reasons is not None else []
    drivers = [
        RiskDriver(
            text=_first_sentence(fact.text),
            level=_SEVERITY_TO_PRIORITY.get(fact.severity or "low", "Low"),
            location=fact.location,
            fact_ids=[fact.id],
        )
        for fact in review_facts or []
        if fact.kind != "coverage"
    ]
    drivers.sort(key=lambda driver: _RANK[driver.level])
    floor = drivers[0].level if drivers else "Low"
    level = floor
    raised = None
    if model is not None and _RANK[model.overall_risk] < _RANK[floor]:
        target = next(name for name, rank in _RANK.items() if rank == _RANK[floor] - 1)
        raised = _model_support(report, target)
        if raised is None:
            log.append(f"risk: the model's {model.overall_risk} has no grounded finding above {floor}")
        else:
            level = target
            drivers.insert(0, raised)
    report.overall_risk = level
    shown = [driver for driver in drivers if _RANK[driver.level] <= _RANK[level] + 1][:RISK_DRIVER_CAP]
    report.risk_drivers = shown
    report.risk_reason = _risk_reason(level, floor, drivers, raised)
    note = (model.risk_reason or "").strip() if model is not None else ""
    if note and model.overall_risk != level:
        # The model argued for a level the evidence does not support; its reasoning is not shown.
        log.append(f"risk_reason: written for {model.overall_risk}, but the evidence supports {level}")
        note = ""
    if note and ground is not None:
        problem = _risk_note_problem(note, level, ground)
        if problem:
            log.append(f"risk_reason: {problem}")
        else:
            report.risk_reason += " " + note
    return report


def stored_review(report: ReviewReport | dict, *, stored, repo, sha) -> ReviewReport:
    """A review read back from storage, ready to show. One stored before the risk had evidence
    attached is reassessed from the rule findings alone."""
    if isinstance(report, dict):
        report = ReviewReport.model_validate(report)
    if not report.risk_drivers:
        facts = build_review_facts(
            symbols=stored.symbols, relationships=stored.relationships, claims=stored.claims,
            evidences=stored.evidences, repo=repo, sha=sha,
        )
        report = assess_risk(report.model_copy(deep=True), facts)
    return finalize_review(report)


def _model_support(report: ReviewReport, target: str) -> RiskDriver | None:
    for bug in report.bugs:
        if bug.source != "model" or not (bug.fact_ids or bug.locations):
            continue
        if target == "Critical" and bug.status != "confirmed":
            continue
        if bug.confidence == "Low":
            continue
        return RiskDriver(
            text=bug.finding,
            level=target,
            location=(bug.locations or [None])[0],
            fact_ids=bug.fact_ids,
            source="model",
        )
    if target == "Critical":
        return None
    for area in report.attention:
        if area.source == "model" and _RANK[area.priority] <= _RANK[target] and (area.fact_ids or area.locations):
            return RiskDriver(
                text=f"{area.area}: {_first_sentence(area.what_could_go_wrong)}",
                level=target,
                location=(area.locations or area.involved or [None])[0],
                fact_ids=area.fact_ids,
                source="model",
            )
    return None


def _risk_reason(level: str, floor: str, drivers: list[RiskDriver], raised: RiskDriver | None) -> str:
    if not drivers:
        return (
            "Low: the analysis found no caller outside the diff that the change affects, no changed failure "
            "without handling, no stale or missing test for changed code, and no changed route, data, or config."
        )
    at_floor = [driver for driver in drivers if driver.level == floor and driver.source == "rules"]
    count = len(at_floor)
    text = f"{floor}: {count} finding{'s' if count != 1 else ''} from the analysis at this level"
    text += f", the first being: {at_floor[0].text.rstrip('.')}." if at_floor else "."
    if raised is not None:
        text = (
            f"{level}: raised one level from {floor} by a finding that cites the diff or a rule fact: "
            f"{raised.text.rstrip('.')}. " + text
        )
    return text


def _risk_note_problem(note: str, level: str, ground) -> str | None:
    from app.explanation.behavioral_changes import _claim_problem

    token = ground.unknown_code(note)
    if token:
        return f"uses {token}, which is not in the diff or the facts"
    other = [name for name in _RANK if name != level and re.search(rf"\b{name}\b", note, re.IGNORECASE)]
    if other:
        return f"claims {other[0]} risk, but the evidence supports {level}"
    return _claim_problem(note, ground.raw)


def _capped_priority(area: AttentionArea, ground) -> str:
    """A model attention area is at most one level above the most severe rule fact it cites, and at most
    Medium when it cites none."""
    cited = [ground.rule_level[i] for i in area.fact_ids if i in ground.rule_level]
    ceiling = min((_RANK[level] for level in cited), default=_RANK["Low"]) - 1
    ceiling = max(ceiling, _RANK["High"]) if cited else _RANK["Medium"]
    return area.priority if _RANK[area.priority] >= ceiling else next(n for n, r in _RANK.items() if r == ceiling)


# --- merge ------------------------------------------------------------------------------------


def merge_review(model: ReviewReport | None, rules: ReviewReport) -> ReviewReport:
    if model is None:
        return rules
    merged = model.model_copy(deep=True)
    for field in ("attention", "questions", "missing_tests", "safe", "top_questions"):
        if not getattr(merged, field):
            setattr(merged, field, list(getattr(rules, field)))
    cited = {fact_id for bug in merged.bugs for fact_id in bug.fact_ids}
    merged.bugs = [*merged.bugs, *[bug for bug in rules.bugs if not set(bug.fact_ids) & cited]][: CAPS["bugs"]]
    merged.undetermined = _dedupe([*merged.undetermined, *rules.undetermined], lambda text: text)[:8]
    return merged


# --- render -----------------------------------------------------------------------------------


# --- what the Review tab shows -------------------------------------------------------------

# Files a reviewer does not need called out as an attention area.
_NOT_REVIEW_FILE = re.compile(r"(\.ya?ml$|(^|/)readme(\.[a-z]+)?$)", re.IGNORECASE)
# Private (leading underscore, JS #name) and dunder names, as a whole word in code or prose.
_PRIVATE_NAME = re.compile(r"(?<![\w.#])(?:_{1,2}[A-Za-z]\w*|#[A-Za-z]\w*)(?=\W|$)")


def finalize_review(report: ReviewReport) -> ReviewReport:
    """Keep only what the Review tab shows: attention areas, bugs, top questions, and the risk.

    Attention areas about private methods, dunder methods such as __repr__, YAML files, or a
    README are dropped, and such entries are removed from the files/functions an area lists.
    """
    out = report.model_copy(deep=True)
    out.questions = []
    out.missing_tests = []
    out.safe = []
    out.undetermined = []
    kept = []
    for area in out.attention:
        if _private_or_ignored(area.area):
            continue
        involved = [entry for entry in area.involved if not _private_or_ignored(entry)]
        if area.involved and not involved:
            continue
        area.involved = involved
        area.locations = [loc for loc in area.locations if not _private_or_ignored(loc)]
        kept.append(area)
    out.attention = kept
    return out


def _private_or_ignored(text: str) -> bool:
    text = text or ""
    for token in re.findall(r"[\w./-]+\.(?:ya?ml|md|rst|txt)\b|[\w./-]*readme[\w.]*", text, re.IGNORECASE):
        path = token.split(":", 1)[0]
        if _NOT_REVIEW_FILE.search(path):
            return True
    head = re.split(r"[\s(:]", text.strip(), maxsplit=1)[0]
    if _NOT_REVIEW_FILE.search(head.split(":", 1)[0]):
        return True
    return bool(_PRIVATE_NAME.search(text.split(" (", 1)[0]))


NONE_FOUND = "None found in the diff and stored facts."


def render_review_markdown(report: ReviewReport | dict | None, *, repo: str | None = None, sha: str | None = None) -> str:
    """Sections in the reviewer's order: attention areas, potential bugs, top questions, then the
    overall risk. Empty sections say so rather than disappearing."""
    if report is None:
        return ""
    if isinstance(report, dict):
        report = ReviewReport.model_validate(report)
    link = lambda loc: _link(loc, repo, sha)  # noqa: E731
    out: list[str] = []

    report = finalize_review(report)

    out.append("### 1. Reviewer attention areas")
    for area in report.attention:
        out.append(f"**{area.priority} · {area.area}**")
        out.append(f"- **Why it matters:** {area.why_it_matters}")
        out.append(f"- **What changed:** {area.what_changed}")
        out.append(f"- **What could go wrong:** {area.what_could_go_wrong}")
        if area.involved:
            out.append("- **Files/functions:** " + ", ".join(link(item) for item in area.involved))
        out.append("")
    if not report.attention:
        out.extend([NONE_FOUND, ""])

    out.append("### 2. Potential bugs and regressions")
    for bug in report.bugs:
        out.append(f"**{bug.finding}** _(confidence {bug.confidence}, {bug.status})_")
        out.append(f"- **Evidence:** {bug.evidence}" + (" — " + ", ".join(link(l) for l in bug.locations) if bug.locations else ""))
        out.append(f"- **Scenario:** {bug.scenario}")
        out.append(f"- **Impact:** {bug.impact}")
        out.append("")
    if not report.bugs:
        out.extend([NONE_FOUND, ""])

    out.append("### 3. Top review questions")
    for index, q in enumerate(report.top_questions, start=1):
        out.append(f"{index}. **{q.question}**")
        out.append(f"   - Why ask this: {q.why_ask}")
        code = ", ".join(link(l) for l in q.locations) or q.relevant_code
        out.append(f"   - Relevant code: {code}")
    if not report.top_questions:
        out.append(NONE_FOUND)
    out.append("")

    out.append(f"### Overall review risk: {report.overall_risk}")
    out.append(report.risk_reason)
    if report.risk_drivers:
        out.extend(["", "**Evidence:**"])
        for driver in report.risk_drivers:
            where = f" — {link(driver.location)}" if driver.location else ""
            origin = " _(model finding)_" if driver.source == "model" else ""
            out.append(f"- **{driver.level}** · {driver.text}{where}{origin}")
    return "\n".join(out).strip()


def _link(location: str, repo: str | None, sha: str | None) -> str:
    match = _LOCATION.match(location or "")
    if not (match and repo and sha):
        return f"`{location}`"
    path, start, end = match.group("path"), match.group("start"), match.group("end")
    anchor = f"#L{start}" + (f"-L{end}" if end else "") if start else ""
    return f"[`{location}`](https://github.com/{repo}/blob/{sha}/{path}{anchor})"


# --- helpers ----------------------------------------------------------------------------------


def _top(question: str, why: str, where: str, fact_id: str) -> TopQuestion:
    return TopQuestion(
        question=question, why_ask=why, relevant_code=where, fact_ids=[fact_id], locations=[where] if where else [], source="rules"
    )


def _returned(statement: str) -> str:
    return re.sub(r"^\s*return\s+", "", statement or "").rstrip().rstrip(";")


def _code(text: str) -> str:
    """Inline code that survives backticks inside the code."""
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def _ranked(items: list[dict]) -> list[dict]:
    from app.analyzer.behavior import CATEGORY_ORDER

    def key(item):
        best = min((CATEGORY_ORDER.get(c["category"], 9) for c in item["changes"]), default=9)
        return (-1 if item.get("removed") else best, not item.get("exported"), -((item.get("reach") or {}).get("outside_diff") or 0))

    return sorted(items, key=key)


def _max(a: str, b: str) -> str:
    return a if _RANK[a] <= _RANK[b] else b


def _dedupe(items: list, key) -> list:
    seen, out = set(), []
    for item in items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out


def _claim_location(claim, evidence_by_id) -> str | None:
    for evidence_id in getattr(claim, "evidence_public_ids", None) or getattr(claim, "evidence_ids", None) or []:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is not None and getattr(evidence, "file", None):
            line = getattr(evidence, "start_line", None)
            return f"{evidence.file}:{line}" if line else evidence.file
    return None


def _normalize(text: str) -> str:
    return " ".join((text or "").replace("\\n", " ").split())


def _id(item):
    return getattr(item, "public_id", None) or getattr(item, "id", None)


def make_ground(*, diff: str, lines_by_file, behavior_facts, impact_facts, review_facts) -> _Ground:
    return _Ground(
        diff=diff, lines_by_file=lines_by_file, behavior_facts=behavior_facts, impact_facts=impact_facts, review_facts=review_facts
    )


# --- entry point ------------------------------------------------------------------------------


def review_system_prompt() -> str:
    from pathlib import Path

    return (
        "You review a pull request from the diff and the facts you are given. You do not see the rest of the "
        "repository. Use only the inputs; never invent files, functions, callers, tests, or behavior. "
        "Return JSON only, matching the output schema. Do not add any other key.\n\n"
        + (Path(__file__).resolve().parent / "prompts" / "review.md").read_text(encoding="utf-8").strip()
    )


def build_review(
    *,
    stored,
    repo: str | None,
    sha: str | None,
    patches: dict[str, str] | None,
    behavior_facts=None,
    impact_facts=None,
    ask_model=None,
    pr_title: str | None = None,
    reasons: list[str] | None = None,
) -> ReviewReport:
    """Rule review, plus the model's review when ``ask_model(system, user, schema) -> str`` is given.

    The model's reply is screened against the diff and the facts; anything ungrounded is dropped,
    and a failed call leaves the rule review.
    """
    log = reasons if reasons is not None else []
    review_facts = build_review_facts(
        symbols=stored.symbols, relationships=stored.relationships, claims=stored.claims, evidences=stored.evidences, repo=repo, sha=sha
    )
    diff, lines_by_file = diff_block(patches)
    rules = rule_review(
        symbols=stored.symbols,
        relationships=stored.relationships,
        claims=stored.claims,
        evidences=stored.evidences,
        repo=repo,
        sha=sha,
        review_facts=review_facts,
        diff_available=bool(diff),
    )
    if ask_model is None:
        return finalize_review(rules)
    user = review_user_message(
        task="Write the review described in the system message.",
        diff=diff,
        behavior_facts=behavior_facts,
        impact_facts=impact_facts,
        review_facts=review_facts,
        pr_title=pr_title,
    )
    try:
        content = ask_model(review_system_prompt(), user, ReviewReport.model_json_schema())
        raw = json.loads(content or "")
    except Exception as exc:  # the review is optional; the rules stand on their own
        log.append(f"review call failed: {type(exc).__name__}")
        return finalize_review(rules)
    ground = make_ground(
        diff=diff, lines_by_file=lines_by_file, behavior_facts=behavior_facts, impact_facts=impact_facts, review_facts=review_facts
    )
    model = screen_review(raw, ground, log)
    merged = merge_review(model, rules)
    return finalize_review(assess_risk(merged, review_facts, model=model, ground=ground, reasons=log))
