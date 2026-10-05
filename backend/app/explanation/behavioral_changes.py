"""Behavioral Changes: how the system behaves differently because of this pull request.

The section is for reviewers. It states observable behavior and functional
outcomes ("Previously X was handled as Y; with this change it is handled as
Z"). It never lists files, functions, methods, or classes.

The text is written by the configured model during the explanation step from
the packet's BEHAVIOR FACTS (``app.explanation.behavior_facts``), and kept only
after ``screen_narrative`` checks each item:

* it cites at least one known fact,
* it names no function, method, or class (any changed function, any function
  a changed statement invokes, any caller), except public entry points in the
  "who notices" line,
* every other code token in it (identifiers, dotted names, backticked text)
  appears in the facts it cites,
* it does not list code edits or assert a defect.

When nothing survives, the section says so rather than falling back to a
statement-level list, because such a list is an implementation detail.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import call_names
from app.explanation.schema import (
    BehaviorChangeNote,
    BehavioralNarrative,
    BehaviorFunctionFact,
    BehaviorWatchNote,
)

CHANGE_CAP = 6
WATCH_CAP = 3

NO_FACTS = "The diff shows no statement-level behavior change."
NO_NARRATIVE = (
    "A behavioral summary is not available for this commit: the model's narration was missing "
    "or did not pass the grounding check."
)

# Product and protocol names that read like identifiers but are not code.
_WORDS = {
    "TypeScript", "JavaScript", "GitHub", "GitLab", "OAuth", "PullRequest", "FastAPI", "PostgreSQL",
    "OpenAI", "MySQL", "SQLite", "GraphQL", "WebSocket", "WebSockets", "iOS", "macOS", "YouTube",
}
_DEFECT = re.compile(r"\b(is insecure|will break|this pr is bad|is broken|is a bug|is wrong)\b", re.IGNORECASE)
_EDIT_LIST = re.compile(
    r"\b(?:add(?:ed|s)?|modif(?:y|ied|ies)|renam(?:ed|es)|refactor(?:ed|s)?|updat(?:ed|es)|remov(?:ed|es))\s+"
    r"(?:the\s+|a\s+|an\s+)?(?:new\s+)?(?:function|method|class|file|module|helper)s?\b",
    re.IGNORECASE,
)
_BACKTICK = re.compile(r"`+\s?([^`]+?)\s?`+")
_DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?![\w(])")
_SNAKE = re.compile(r"(?<![\w.])_?[a-z][a-z0-9]*_[a-z0-9_]+\b")
_CAMEL = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b|\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")
_CALL_LIKE = re.compile(r"\b[A-Za-z_][\w.]*\(")
_PLAIN_WORD = re.compile(r"^[a-z]+$")
# A source file path such as backend/app/api.py or src/session.ts. Routes like /api/runs are not files.
_FILE_PATH = re.compile(r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx|js|jsx|go|java|rb|rs|cs|php|kt|swift|vue|css|scss|sql|toml|ya?ml|json|md)\b")


# --- section -----------------------------------------------------------------------


def build_behavioral_section(
    *,
    narrative: BehavioralNarrative | dict | None,
    facts: list[BehaviorFunctionFact],
    prescreened: bool = False,
    reasons: list[str] | None = None,
) -> dict:
    """The section the Explain tab and the comment show.

    A narrative stored by the explanation step was already screened against the packet facts it was
    written from, so callers reading it back pass prescreened=True.
    """
    if prescreened and narrative:
        chosen = narrative if isinstance(narrative, BehavioralNarrative) else _parse(narrative)
    else:
        chosen = screen_narrative(narrative, facts) if narrative else None
    fact_count = sum(len(fact.changes) for fact in facts)
    if chosen is None or not chosen.changes:
        overview = NO_FACTS
        if fact_count:
            why = [reason for reason in (reasons or []) if reason]
            overview = NO_NARRATIVE + (f" Reason: {why[0]}." if why else "")
        return {
            "source": "none",
            "fact_count": fact_count,
            "overview": overview,
            "changes": [],
            "watch": [],
        }
    return {
        "source": "model",
        "fact_count": fact_count,
        "overview": chosen.overview,
        "changes": [
            {"title": change.title, "before": change.before, "after": change.after, "impact": change.impact}
            for change in chosen.changes
        ],
        "watch": [note.text for note in chosen.watch],
    }


def render_behavioral_changes_markdown(section: dict) -> str:
    lines = ["### Behavioral Changes", ""]
    changes = section.get("changes") or []
    if not changes:
        lines.append(section.get("overview") or NO_FACTS)
        return "\n".join(lines)
    if section.get("overview"):
        lines.extend([section["overview"], ""])
    for change in changes:
        lines.append(f"**{change['title']}**")
        lines.append(f"- **Before:** {change['before']}")
        lines.append(f"- **After:** {change['after']}")
        if change.get("impact"):
            lines.append(f"- **Who notices:** {change['impact']}")
        lines.append("")
    watch = section.get("watch") or []
    if watch:
        lines.append("**Worth checking**")
        lines.extend(f"- {item}" for item in watch)
        lines.append("")
    lines.append(
        f"_Written by the configured model from {section.get('fact_count', 0)} before-and-after facts in the diff; "
        "each item was checked against the facts it cites._"
    )
    return "\n".join(lines).strip()


# --- screening ----------------------------------------------------------------------------


def screen_narrative(
    narrative: BehavioralNarrative | dict | None,
    facts: list[BehaviorFunctionFact],
    reasons: list[str] | None = None,
) -> BehavioralNarrative | None:
    """Keep only items that describe outcomes, cite facts, and use no code names beyond those facts.

    Drop reasons are appended to ``reasons`` when a list is passed, for the pipeline event log.
    """
    log = reasons if reasons is not None else []
    if narrative is None:
        log.append("the model returned no behavioral_changes")
        return None
    if not facts:
        log.append("the packet had no behavior facts")
        return None
    if isinstance(narrative, dict):
        narrative = _parse(narrative)
        if narrative is None:
            log.append("behavioral_changes did not match the schema")
            return None

    grounding = Grounding(facts)
    by_change = grounding.by_change
    scope = grounding.scope
    problem = grounding.problem

    kept: list[BehaviorChangeNote] = []
    for change in narrative.changes:
        allowed = scope(change.fact_ids)
        if allowed is None:
            log.append(f"dropped '{change.title[:40]}': cites no known fact")
            continue
        issue = None
        for text, entries_ok in ((change.title, False), (change.before, False), (change.after, False), (change.impact or "", True)):
            if text == "" and entries_ok:
                continue
            issue = problem(text, allowed, may_name_entries=entries_ok)
            if issue:
                break
        if issue:
            log.append(f"dropped '{change.title[:40]}': {issue}")
            continue
        kept.append(change.model_copy(update={"fact_ids": grounding.known(change.fact_ids)}))
        if len(kept) >= CHANGE_CAP:
            break
    if not kept:
        return None

    watch: list[BehaviorWatchNote] = []
    for note in narrative.watch:
        allowed = scope(note.fact_ids)
        if allowed is None or "?" not in note.text:
            continue
        if problem(note.text, allowed, may_name_entries=True) is None:
            watch.append(note)
        if len(watch) >= WATCH_CAP:
            break

    overview = narrative.overview.strip()
    everything = scope(list(by_change)) or ""
    if overview and problem(overview, everything, may_name_entries=True):
        overview = ""
    return BehavioralNarrative(overview=overview, changes=kept, watch=watch)


class Grounding:
    """What a narrative item may say, given the behavior facts (b…) and impact facts (i…) it cites."""

    def __init__(self, facts: list[BehaviorFunctionFact], impact_facts=None) -> None:
        self.by_change = {change.id: (fact, change) for fact in facts for change in fact.changes}
        self.by_impact = {item.id: item for item in impact_facts or []}
        self.callables = _callable_names(facts)
        self.entry_points = {name for fact in facts for name in fact.reached_from}

    def known(self, ids: list[str]) -> list[str]:
        return [item for item in ids if item in self.by_change or item in self.by_impact]

    def scope(self, ids: list[str]) -> str | None:
        valid = self.known(ids)
        if not valid:
            return None
        chunks: list[str] = []
        for item_id in valid:
            if item_id in self.by_impact:
                impact = self.by_impact[item_id]
                chunks.append(impact.text)
                for behavior_id in impact.behavior_ids:
                    chunks.extend(self._behavior_chunks(behavior_id))
            else:
                chunks.extend(self._behavior_chunks(item_id))
        return "\n".join(chunks)

    def _behavior_chunks(self, change_id: str) -> list[str]:
        if change_id not in self.by_change:
            return []
        fact, change = self.by_change[change_id]
        chunks = list(fact.reached_from + fact.notes)
        chunks.extend(text for text in (change.before, change.after, change.before_when, change.after_when) if text)
        return chunks

    def problem(self, text: str, allowed: str, *, may_name_entries: bool) -> str | None:
        if not text.strip():
            return "empty text"
        if _DEFECT.search(text):
            return "asserted a defect"
        if _EDIT_LIST.search(text):
            return "listed code edits"
        if _CALL_LIKE.search(text):
            return "quoted a call"
        if _FILE_PATH.search(text):
            return "named a file"
        permitted = self.entry_points if may_name_entries else set()
        for name in self.callables - permitted:
            # A plain word such as "charge" or "record" is also English; it counts as a name only in code form.
            if _PLAIN_WORD.match(name):
                continue
            if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text):
                return f"named the function {name}"
        for token in _identifiers(text):
            if token in _WORDS or token in permitted:
                continue
            if token in self.callables:
                return f"named the function {token}"
            if token not in allowed:
                return f"used {token}, which is not in the cited facts"
        return None


def _callable_names(facts: list[BehaviorFunctionFact]) -> set[str]:
    """Every function or method name the facts mention: changed functions, callers, and invoked names."""
    names: set[str] = set()
    for fact in facts:
        if fact.function:
            names.add(fact.function)
        names.update(fact.reached_from)
        for site in fact.callers_at_head:
            caller, _, call = site.partition(" → ")
            names.add(caller.strip())
            names.update(_split_call(call))
        for change in fact.changes:
            for text in (change.before, change.after):
                for call in call_names(text or ""):
                    names.update(_split_call(call))
    return {name for name in names if name and len(name) > 2}


def _split_call(call: str) -> list[str]:
    """`audit.record` → [`audit.record`, `record`] so either spelling counts as a function name."""
    head = call.split("(", 1)[0].strip()
    if not head:
        return []
    parts = [head]
    if "." in head:
        parts.append(head.rsplit(".", 1)[-1])
    return parts


def _parse(narrative: dict) -> BehavioralNarrative | None:
    try:
        return BehavioralNarrative.model_validate(narrative)
    except Exception:
        return None


def _identifiers(text: str) -> list[str]:
    tokens = [match.strip() for match in _BACKTICK.findall(text)]
    plain = _BACKTICK.sub(" ", text)
    tokens += _DOTTED.findall(plain) + _SNAKE.findall(plain) + _CAMEL.findall(plain)
    return [token for token in tokens if token]
