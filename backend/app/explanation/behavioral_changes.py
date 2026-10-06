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
* it does not list code edits or assert a defect,
* every number and quoted value in it appears in the facts it cites,
* it claims a failure (an error, a rejection, a crash) only when a cited fact shows one,
* it talks about the same thing as the facts it cites: at least one content word of the
  item appears in them (identifiers count by their words, so "invalid order" matches
  ``InvalidOrder``),
* the "who notices" line names only entry points that reach the functions it cites.

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
    "No behavior summary could be verified against the diff for this commit. "
    "The changed functions are listed under Details › Key Changes."
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
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
_QUOTED = re.compile(r"[\"“]([^\"”]{2,})[\"”]")
_FAILURE_CLAIM = re.compile(
    r"\b(?:fail(?:s|ed|ure|ures|ing)?|crash(?:es|ed)?|breaks?|broken|reject(?:s|ed|ion)?|throws?|thrown|"
    r"rais(?:e|es|ed)|exceptions?|errors?|denied|refus(?:e|es|ed))\b",
    re.IGNORECASE,
)
_FAILURE_EVIDENCE = re.compile(
    r"raise|throw|error|exception|fail|reject|invalid|den(?:y|ied)|refus|abort|panic|missing|requires|required|"
    r"status|\b[45]\d\d\b|return None|return null|return false|return -1",
    re.IGNORECASE,
)
# Areas a reviewer reads as a claim about the system. Naming one requires a cited fact about it:
# a PR that touches no session code must not be summarized as "session handling".
_DOMAIN = {
    "auth": (r"auth\b", "authent", "authoriz", "oauth", "login", "logout", "signin", "sign-in", "credential", "password"),
    "session": ("session", "cookie"),
    "token": ("token", "jwt"),
    "timeout": ("timeout", "time-out", "expir", "ttl"),
    "permission": ("permission", "role", "admin", "privilege", "access control"),
    "security": ("secur", "encrypt", "privacy", "vulnerab", "attack"),
    "payment": ("payment", "billing", "invoice"),
    "database": ("database", "migration"),
    "cache": ("cache", "caching"),
    "validation": ("validat", "sanitiz"),
    "rate limit": ("rate limit", "rate-limit", "throttl"),
    "notification": ("email", "notification", "webhook"),
}
# Evaluations of quality or intent. The facts are statements from the diff; they never show that a
# change is safer, faster, or better, so these are dropped unless a cited fact uses the same word.
_JUDGMENT = re.compile(
    r"\b(?:strong(?:er|est)|weak(?:er|est)|safer|secure(?:ly)?|security|improv(?:e|es|ed|ing|ement|ements)|better|worse|"
    r"responsive(?:ness)?|performan(?:t|ce)|faster|slower|efficien(?:t|cy)|robust(?:ness)?|reliab(?:le|ility)|"
    r"best practices?|ensur(?:e|es|ed|ing)|guarantee(?:s|d)?|protect(?:s|ed|ion)?|harden(?:s|ed|ing)?|"
    r"intend(?:s|ed)|aims? to|designed to|cleaner|simpler|maintainab(?:le|ility))\b",
    re.IGNORECASE,
)


# Words that say nothing about what changed, so they never count as shared topic.
_STOP = {
    "previously", "handled", "handle", "handles", "change", "changes", "changed", "with", "this", "that", "they",
    "them", "were", "when", "what", "will", "from", "have", "been", "does", "into", "only", "also", "than", "then",
    "there", "their", "which", "would", "could", "should", "being", "after", "before", "under", "every", "each",
    "some", "more", "less", "same", "other", "instead", "still", "longer", "case", "cases", "outcome", "situation",
    "behavior", "behaviour", "behaves", "system", "users", "user", "code", "value", "values", "result", "results",
    "return", "returns", "returned", "call", "calls", "called", "caller", "callers", "function", "functions",
    "now", "new", "old", "pull", "request", "notice", "notices", "anyone", "everyone", "existing", "path",
    "paths", "flow", "flows", "work", "works", "make", "makes", "made", "true", "false", "none", "null", "self",
}
# A source file path such as backend/app/api.py or src/session.ts. Routes like /api/runs are not files.
_FILE_PATH = re.compile(r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx|js|jsx|go|java|rb|rs|cs|php|kt|swift|vue|css|scss|sql|toml|ya?ml|json|md)\b")


# --- section -----------------------------------------------------------------------


def build_behavioral_section(
    *,
    narrative: BehavioralNarrative | dict | None,
    facts: list[BehaviorFunctionFact],
    prescreened: bool = False,
    reasons: list[str] | None = None,
    surfaces: list[dict] | None = None,
    system: dict | None = None,
) -> dict:
    """The section the Explain tab and the comment show.

    The narrative is screened again against the facts rebuilt from the stored analysis, even when it
    was screened when it was written (``prescreened``), so a stored narrative never outlives the facts
    it rests on. Each change carries links to the diff lines its facts come from.
    """
    del prescreened
    from app.explanation.plain_summary import behavior_overview

    # Nothing written by the model is shown here. A model can describe things the diff does not
    # contain (an "order confirmation" in a PR with no orders), and screening words cannot rule that
    # out, so the section is written by rule from the PR's facts only.
    del narrative, reasons
    chosen = None
    fact_count = sum(len(fact.changes) for fact in facts)
    # The overall summary is written by rule from the PR's facts, so it cannot claim anything the diff
    # does not show; the items below it are the model's (when they pass the checks) or the rule's.
    summary = behavior_overview(facts, surfaces, system)
    if chosen is None or not chosen.changes:
        rule_changes = _rule_changes(facts, surfaces, system)
        if rule_changes:
            # No model summary passed the checks: show the before/after facts themselves, which are
            # read straight from the diff.
            return {
                "source": "rules",
                "fact_count": fact_count,
                "overview": summary or RULES_NOTE,
                "changes": rule_changes,
                "watch": [],
            }
        return {
            "source": "none",
            "fact_count": fact_count,
            "overview": NO_FACTS,
            "changes": [],
            "watch": [],
        }
    return {
        "source": "model",
        "fact_count": fact_count,
        "overview": summary or chosen.overview,
        "changes": [
            {
                "title": change.title,
                "before": change.before,
                "after": change.after,
                "impact": change.impact,
                "evidence": evidence_links(change.fact_ids, facts),
            }
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
    lines.extend(["<details>", f"<summary>By flow and interface ({len(changes)})</summary>", ""])
    for change in changes:
        lines.append(f"**{change['title']}**")
        lines.append(f"- **Before:** {change['before']}")
        lines.append(f"- **After:** {change['after']}")
        if change.get("evidence"):
            lines.append("- **Evidence:** " + ", ".join(_evidence_markdown(item) for item in change["evidence"]))
        lines.append("")
    lines.extend(["</details>", ""])
    watch = section.get("watch") or []
    if watch:
        lines.append("**Worth checking**")
        lines.extend(f"- {item}" for item in watch)
        lines.append("")
    if section.get("source") == "model":
        lines.append(
            f"_Written by the configured model from {section.get('fact_count', 0)} before-and-after facts in the diff; "
            "each item was checked against the facts it cites._"
        )
    return "\n".join(lines).strip()


def evidence_links(fact_ids: list[str], facts: list[BehaviorFunctionFact], impact_facts=None, cap: int = 4) -> list[dict]:
    """Diff locations of the behavior facts an item cites (impact facts count by their behavior ids)."""
    by_impact = {item.id: item for item in impact_facts or []}
    by_change = {change.id: change for fact in facts for change in fact.changes}
    ids: list[str] = []
    for item_id in fact_ids:
        ids.extend(by_impact[item_id].behavior_ids if item_id in by_impact else [item_id])
    out: list[dict] = []
    for item_id in ids:
        change = by_change.get(item_id)
        if change is None or not change.location or any(entry["label"] == change.location for entry in out):
            continue
        out.append({"label": change.location, "href": change.href})
        if len(out) >= cap:
            break
    return out


def _evidence_markdown(item: dict) -> str:
    label = f"`{item['label']}`"
    return f"[{label}]({item['href']})" if item.get("href") else label


RULES_NOTE = "Summarized by flow and by system interface from the changes in the diff."


def _rule_changes(facts: list[BehaviorFunctionFact], surfaces: list[dict] | None = None, system: dict | None = None) -> list[dict]:
    from app.explanation.plain_summary import rule_behavior_items

    return rule_behavior_items(facts, lambda ids: evidence_links(ids, facts), surfaces, system)


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
        change = change.model_copy(
            update={
                field: grounding.strip_judgments(getattr(change, field), change.fact_ids)
                for field in ("title", "before", "after", "impact")
            }
        )
        if not (change.title and change.before and change.after):
            log.append(f"dropped an item: only judged the change")
            continue
        entries = grounding.entries_for(change.fact_ids)
        issue = None
        for text, entries_ok in ((change.title, False), (change.before, False), (change.after, False), (change.impact or "", True)):
            if text == "" and entries_ok:
                continue
            issue = problem(text, allowed, may_name_entries=entries_ok, entries=entries)
            if issue:
                break
        if issue is None and grounding.off_topic([change.title, change.before, change.after, change.impact], change.fact_ids):
            issue = "shares no subject with the facts it cites"
        if issue is None:
            issue = grounding.unsupported_claim(
                " ".join([change.title, change.before, change.after, change.impact or ""]), change.fact_ids
            )
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
        if (
            problem(note.text, allowed, may_name_entries=True, entries=grounding.entries_for(note.fact_ids)) is None
            and not grounding.off_topic([note.text], note.fact_ids)
            and grounding.unsupported_claim(note.text, note.fact_ids) is None
        ):
            watch.append(note)
        if len(watch) >= WATCH_CAP:
            break

    kept_ids = [fact_id for change in kept for fact_id in change.fact_ids]
    overview = grounding.strip_judgments(narrative.overview.strip(), kept_ids)
    everything = scope(list(by_change)) or ""
    # The overview may only summarize the kept changes: it is checked against their facts alone.
    if overview and (
        problem(overview, everything, may_name_entries=True)
        or grounding.off_topic([overview], kept_ids)
        or grounding.unsupported_claim(overview, kept_ids)
    ):
        log.append("dropped the overview: it goes beyond the kept changes")
        overview = ""
    return BehavioralNarrative(overview=overview, changes=kept, watch=watch)


class Grounding:
    """What a narrative item may say, given the behavior facts (b…) and impact facts (i…) it cites."""

    def __init__(self, facts: list[BehaviorFunctionFact], impact_facts=None) -> None:
        self.by_change = {change.id: (fact, change) for fact in facts for change in fact.changes}
        self.by_impact = {item.id: item for item in impact_facts or []}
        self.callables = _callable_names(facts)
        self.entry_points = {name for fact in facts for name in fact.reached_from}

    def entries_for(self, ids: list[str]) -> set[str]:
        """Entry points that reach the functions the cited facts belong to."""
        names: set[str] = set()
        for item_id in self.known(ids):
            behavior_ids = self.by_impact[item_id].behavior_ids if item_id in self.by_impact else [item_id]
            for behavior_id in behavior_ids:
                if behavior_id in self.by_change:
                    names.update(self.by_change[behavior_id][0].reached_from)
        return names

    def topic_text(self, ids: list[str]) -> str:
        """Everything the cited facts are about: their statements plus the names of the functions,
        files, callers, and entry points involved. Used only for the subject check, never to allow a
        name in the text."""
        chunks: list[str] = []
        for item_id in self.known(ids):
            if item_id in self.by_impact:
                impact = self.by_impact[item_id]
                chunks.append(impact.text)
                behavior_ids = impact.behavior_ids
            else:
                behavior_ids = [item_id]
            for behavior_id in behavior_ids:
                if behavior_id not in self.by_change:
                    continue
                fact, _change = self.by_change[behavior_id]
                chunks.extend([fact.function, fact.file, *fact.callers_at_head, *fact.tests])
                chunks.extend(self._behavior_chunks(behavior_id))
        return "\n".join(chunk for chunk in chunks if chunk)

    def all_ids(self) -> list[str]:
        return [*self.by_change, *self.by_impact]

    def strip_judgments(self, text: str, ids: list[str]) -> str:
        """Drop the sentences that judge the change (safer, improves, ensures, …) when no cited fact uses
        that word. The rest of the item stays."""
        topic = self.topic_text(ids).casefold()
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
            judged = _JUDGMENT.search(sentence)
            if judged and judged.group(0).casefold() not in topic:
                continue
            kept.append(sentence)
        return " ".join(kept).strip()

    def unsupported_claim(self, text: str, ids: list[str]) -> str | None:
        """A system area no fact of this pull request is about, or a judgment the cited facts do not show."""
        topic = self.topic_text(ids).casefold()
        everything = self.topic_text(self.all_ids()).casefold()
        lowered = (text or "").casefold()
        judged = _JUDGMENT.search(text or "")
        if judged and judged.group(0).casefold() not in topic:
            return f'judged the change ("{judged.group(0)}"), which the facts cannot show'
        topic = everything
        for area, stems in _DOMAIN.items():
            said = next((stem for stem in stems if re.search(rf"\b{stem}", lowered)), None)
            if said and not any(re.search(stem.replace(r"\b", ""), topic) for stem in stems):
                return f'mentioned {area} ("{said.replace(chr(92) + "b", "")}…"), which no cited fact is about'
        return None

    def off_topic(self, texts: list[str], ids: list[str]) -> bool:
        """True when none of the item's content words matches anything the cited facts are about.

        Words match when one is a prefix of the other ("sess" and "session", "expir" and "expiry")."""
        said = set().union(*(_content_words(text) for text in texts if text)) if texts else set()
        if not said:
            return False
        for scope_ids in (ids, self.all_ids()):
            facts = _content_words(self.topic_text(scope_ids))
            if any(a.startswith(b) or b.startswith(a) for a in said for b in facts):
                return False
        return True

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

    def problem(
        self,
        text: str,
        allowed: str,
        *,
        may_name_entries: bool,
        allow: set[str] | None = None,
        entries: set[str] | None = None,
    ) -> str | None:
        if not text.strip():
            return "empty text"
        claim = _claim_problem(text, allowed)
        if claim:
            return claim
        if _DEFECT.search(text):
            return "asserted a defect"
        if _EDIT_LIST.search(text):
            return "listed code edits"
        if _CALL_LIKE.search(text):
            return "quoted a call"
        if _FILE_PATH.search(text):
            return "named a file"
        reachable = self.entry_points if entries is None else entries
        permitted = (reachable if may_name_entries else set()) | (allow or set())
        for name in self.callables - permitted:
            # A plain word such as "charge" or "record" is also English; it counts as a name only in code form.
            if _PLAIN_WORD.match(name):
                continue
            if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text):
                if may_name_entries and name in self.entry_points:
                    return f"named {name}, which does not reach the cited change"
                return f"named the function {name}"
        for token in _identifiers(text):
            if token in _WORDS or token in permitted:
                continue
            if may_name_entries and token in self.entry_points:
                return f"named {token}, which does not reach the cited change"
            if token in self.callables:
                return f"named the function {token}"
            if token not in allowed:
                return f"used {token}, which is not in the cited facts"
        return None


def _claim_problem(text: str, allowed: str) -> str | None:
    """Numbers, quoted values, and failures must come from the cited facts."""
    allowed_numbers = set(_NUMBER.findall(allowed))
    for number in _NUMBER.findall(text):
        if number not in allowed_numbers:
            return f"stated the number {number}, which is not in the cited facts"
    lowered = allowed.casefold()
    for quoted in _QUOTED.findall(text):
        if quoted.strip().casefold() not in lowered:
            return f'quoted "{quoted[:30]}", which is not in the cited facts'
    claim = _FAILURE_CLAIM.search(text)
    if claim and not _FAILURE_EVIDENCE.search(allowed):
        return f'claimed a failure ("{claim.group(0)}") that no cited fact shows'
    return None


def _content_words(text: str) -> set[str]:
    """Lower-case word stems of length 4+, with identifiers split into their words."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    words = re.findall(r"[A-Za-z]+", spaced.replace("_", " "))
    return {_stem(word.lower()) for word in words if len(word) >= 4 and word.lower() not in _STOP}


def _stem(word: str) -> str:
    for suffix in ("ations", "ation", "ings", "ing", "ied", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


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
