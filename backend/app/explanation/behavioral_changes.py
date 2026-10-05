"""Behavioral Changes: how the system behaves differently because of this pull request.

The section is written for a lead reviewer. It describes observable outcomes
("Previously X was handled as Y; with this change it is handled as Z"), not
the list of edited files or functions.

Two sources, both grounded in the same behavior facts
(``app.explanation.behavior_facts``):

* the configured model's ``behavioral_changes`` narrative, written during the
  explanation step from the packet's BEHAVIOR FACTS and kept only after
  ``screen_narrative`` checks every change against the facts it cites, and
* a deterministic summary of the same facts, used when the model narrative is
  missing or nothing in it survives screening.
"""

from __future__ import annotations

import re

from app.analyzer.behavior import call_names, error_target
from app.explanation.behavior_facts import param_delta
from app.explanation.schema import (
    BehaviorChangeNote,
    BehavioralNarrative,
    BehaviorFunctionFact,
    BehaviorWatchNote,
)

CHANGE_CAP = 6
WATCH_CAP = 3
PARTS_PER_CHANGE = 3

_DEFECT = re.compile(r"\b(is insecure|will break|this pr is bad|is broken|is a bug|is wrong)\b", re.IGNORECASE)
_EDIT_LIST = re.compile(
    r"\b(?:add(?:ed|s)?|modif(?:y|ied|ies)|renam(?:ed|es)|refactor(?:ed|s)?|updat(?:ed|es))\s+"
    r"(?:the\s+|a\s+|an\s+)?(?:new\s+)?(?:function|method|class|file|module)s?\b",
    re.IGNORECASE,
)
_BACKTICK = re.compile(r"`+\s?([^`]+?)\s?`+")
_DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?![\w(])")
_SNAKE = re.compile(r"(?<![\w.])_?[a-z][a-z0-9]*_[a-z0-9_]+\b")
_CAMEL = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b|\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")


# --- section -----------------------------------------------------------------------


def build_behavioral_section(
    *,
    narrative: BehavioralNarrative | dict | None,
    facts: list[BehaviorFunctionFact],
    prescreened: bool = False,
) -> dict:
    """The section the Explain tab and the comment show.

    A narrative stored by the explanation step was already screened against the packet facts it was
    written from, so callers reading it back pass prescreened=True.
    """
    if prescreened and narrative:
        screened = narrative if isinstance(narrative, BehavioralNarrative) else _parse(narrative)
        if screened is not None and not screened.changes:
            screened = None
    else:
        screened = screen_narrative(narrative, facts) if narrative else None
    if screened is not None:
        source = "model"
        chosen = screened
    else:
        source = "facts"
        chosen = summarize_facts(facts)
    return {
        "source": source,
        "fact_count": sum(len(fact.changes) for fact in facts),
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
        lines.append(section.get("overview") or "The diff shows no statement-level behavior change.")
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
    if section.get("source") == "model":
        lines.append(f"_Narrated by the configured model from {section.get('fact_count', 0)} before-and-after facts in the diff; every item was checked against the facts it cites._")
    else:
        lines.append(f"_Summarized directly from {section.get('fact_count', 0)} before-and-after facts in the diff._")
    return "\n".join(lines).strip()


# --- screening the model narrative ---------------------------------------------------


def screen_narrative(narrative: BehavioralNarrative | dict | None, facts: list[BehaviorFunctionFact]) -> BehavioralNarrative | None:
    """Keep only changes and watch items whose names and code all come from the facts they cite."""
    if narrative is None or not facts:
        return None
    if isinstance(narrative, dict):
        try:
            narrative = BehavioralNarrative.model_validate(narrative)
        except Exception:
            return None
    by_change = {change.id: fact for fact in facts for change in fact.changes}
    change_by_id = {change.id: change for fact in facts for change in fact.changes}
    private_names = {fact.function for fact in facts if not fact.public and fact.function}
    all_names = {fact.function for fact in facts if fact.function}

    def allowed_for(ids: list[str]) -> tuple[str, set[str]] | None:
        valid = [item for item in ids if item in by_change]
        if not valid:
            return None
        chunks: list[str] = []
        cited_functions: set[str] = set()
        for change_id in valid:
            fact = by_change[change_id]
            change = change_by_id[change_id]
            if fact.public:
                cited_functions.add(fact.function)
                chunks.append(fact.function)
            chunks.extend(fact.reached_from + fact.callers_at_head + fact.notes + fact.tests)
            chunks.extend(text for text in (change.before, change.after, change.before_when, change.after_when) if text)
        return "\n".join(chunks), cited_functions

    def grounded(text: str, allowed: str, cited_functions: set[str]) -> bool:
        if not text.strip() or _DEFECT.search(text) or _EDIT_LIST.search(text):
            return False
        for name in private_names | (all_names - cited_functions):
            if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text):
                return False
        for token in _identifiers(text):
            if token not in allowed:
                return False
        return True

    kept: list[BehaviorChangeNote] = []
    for change in narrative.changes:
        scope = allowed_for(change.fact_ids)
        if scope is None:
            continue
        allowed, cited = scope
        if not change.before.strip() or not change.after.strip():
            continue
        texts = [change.title, change.before, change.after, change.impact or ""]
        if not all(grounded(text, allowed, cited) for text in texts if text):
            continue
        if not change.title.strip():
            continue
        kept.append(change.model_copy(update={"fact_ids": [item for item in change.fact_ids if item in by_change]}))
        if len(kept) >= CHANGE_CAP:
            break
    if not kept:
        return None

    watch: list[BehaviorWatchNote] = []
    for note in narrative.watch:
        scope = allowed_for(note.fact_ids)
        if scope is None or "?" not in note.text:
            continue
        allowed, cited = scope
        if grounded(note.text, allowed, cited):
            watch.append(note)
        if len(watch) >= WATCH_CAP:
            break

    overview = narrative.overview.strip()
    every = allowed_for(list(by_change))
    public_functions = every[1] if every else set()
    if overview and not (every and grounded(overview, every[0], public_functions)):
        overview = summarize_facts(facts).overview
    return BehavioralNarrative(overview=overview, changes=kept, watch=watch)


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


# --- deterministic summary --------------------------------------------------------------


def summarize_facts(facts: list[BehaviorFunctionFact]) -> BehavioralNarrative:
    """Outcome sentences straight from the facts, grouped by the public entry points that see them."""
    groups: dict[tuple[str, ...], dict] = {}
    order: list[tuple[str, ...]] = []
    hidden = 0
    for fact in facts:
        if fact.public:
            key = ("fn", fact.function)
        elif fact.reached_from:
            key = ("via", *sorted(fact.reached_from))
        else:
            hidden += len(fact.changes)
            continue
        if key not in groups:
            groups[key] = {"facts": [], "entries": []}
            order.append(key)
        groups[key]["facts"].append(fact)
        for entry in fact.reached_from:
            if entry not in groups[key]["entries"]:
                groups[key]["entries"].append(entry)

    changes: list[BehaviorChangeNote] = []
    watch: list[BehaviorWatchNote] = []
    for key in order:
        group = groups[key]
        parts: list[tuple[int, str, str, str]] = []
        for fact in group["facts"]:
            subject = _code(fact.function) if fact.public else "the shared logic behind these entry points"
            for change in fact.changes:
                phrased = _phrase(change, fact, subject)
                if phrased:
                    parts.append((*phrased, change.id))
        if not parts:
            continue
        parts.sort(key=lambda item: item[0])
        chosen = parts[:PARTS_PER_CHANGE]
        before = "Previously, " + "; ".join(_lower_first(p[1]) for p in chosen) + "."
        after = "With this change, " + "; ".join(_lower_first(p[2]) for p in chosen) + "."
        extra = len(parts) - len(chosen)
        if extra > 0:
            after += f" ({extra} smaller change{'s' if extra != 1 else ''} not listed.)"
        entries = group["entries"]
        if key[0] == "fn":
            title = f"Requests that go through {_code(key[1])}" if entries else f"Callers of {_code(key[1])}"
        else:
            title = f"Requests entering through {_names(entries)}"
        impact_parts = []
        if entries:
            impact_parts.append(f"Anything entering through {_names(entries, 5)}.")
        for fact in group["facts"]:
            impact_parts.extend(fact.notes)
        changes.append(
            BehaviorChangeNote(
                title=title,
                before=before,
                after=after,
                impact=" ".join(impact_parts),
                fact_ids=[p[3] for p in chosen],
            )
        )
        for fact in group["facts"]:
            risky = [c for c in fact.changes if c.kind in {"error", "signature", "return"}]
            if fact.public and not fact.tests and risky and not fact.removed:
                watch.append(
                    BehaviorWatchNote(
                        text=f"No stored test references {_code(fact.function)}; is its new behavior exercised anywhere?",
                        fact_ids=[risky[0].id],
                    )
                )
            for note in fact.notes:
                if "passes" in note:
                    watch.append(BehaviorWatchNote(text=f"Do these callers still work: {note}?", fact_ids=[fact.changes[0].id]))
    changes.sort(key=lambda change: -len(change.fact_ids))

    entries_all = _unique([entry for key in order for entry in groups[key]["entries"]])
    shown = changes[:CHANGE_CAP]
    if shown:
        overview = f"This pull request changes behavior on {len(shown)} path{'s' if len(shown) != 1 else ''}"
        overview += f" reached from {_names(entries_all, 5)}." if entries_all else "."
    else:
        overview = "The diff shows no statement-level behavior change on a public path."
    if hidden:
        overview += f" {hidden} change{'s' if hidden != 1 else ''} in internal code that no public entry point reaches {'are' if hidden != 1 else 'is'} not summarized."
    return BehavioralNarrative(overview=overview, changes=shown, watch=watch[:WATCH_CAP])


def _phrase(change, fact: BehaviorFunctionFact, subject: str) -> tuple[int, str, str] | None:
    """(priority, before, after) outcome phrases for one fact change, or None if it has no clear outcome."""
    b, a = change.before, change.after
    bw, aw = _when(change.before_when), _when(change.after_when)
    kind = change.kind
    if kind == "removed_function":
        return 0, f"{_code(fact.function)} was available to callers", f"{_code(fact.function)} no longer exists"
    if kind == "signature" and b and a:
        added, removed, defaults = param_delta(b, a, fact.file)
        bits = []
        required = [n for n, opt in added if not opt]
        optional = [n for n, opt in added if opt]
        if required:
            bits.append("callers must pass " + ", ".join(_code(n) for n in required))
        if optional:
            bits.append("callers can pass " + ", ".join(_code(n) for n in optional))
        if removed:
            bits.append("callers no longer pass " + ", ".join(_code(n) for n in removed))
        for name, old, new in defaults:
            bits.append(f"omitting {_code(name)} now means {_code(new)} instead of {_code(old)}")
        if not bits:
            return None
        before_bits = []
        if added:
            before_bits.append(f"{subject} accepted no {', '.join(_code(n) for n, _ in added)}")
        if removed:
            before_bits.append(f"callers passed {', '.join(_code(n) for n in removed)}")
        for name, old, _new in defaults:
            before_bits.append(f"omitting {_code(name)} meant {_code(old)}")
        return 1, "; ".join(before_bits), f"for {subject}, " + "; ".join(bits)
    if kind == "error":
        if b and a:
            return 2, f"{subject} failed with {error_target(b)}{bw}", f"it fails with {error_target(a)}{aw}"
        if a:
            return 2, f"{subject} did not fail with {error_target(a)}{aw}", f"{subject} fails with {error_target(a)}{aw}"
        if b:
            return 2, f"{subject} failed with {error_target(b)}{bw}", f"{subject} no longer fails with {error_target(b)}"
    if kind == "return":
        if a and change.after_when and not b:
            return 3, f"{subject} kept going{aw}", f"{subject} stops early and returns {_code(_expr(a))}{aw}"
        if b and change.before_when and not a:
            return 3, f"{subject} stopped early and returned {_code(_expr(b))}{bw}", f"{subject} keeps going{bw}"
        if b and a:
            return 4, f"{subject} produced {_code(_expr(b))}{bw}", f"it produces {_code(_expr(a))}{aw}"
        return None
    if kind in {"call", "logging"}:
        old, new = call_names(b or ""), call_names(a or "")
        gained = [n for n in new if n not in old]
        lost = [n for n in old if n not in new]
        if gained and lost:
            return 5, f"{subject} ran {_names(lost)}{bw}", f"it runs {_names(gained)} instead{aw}"
        if gained:
            return 5, f"{subject} did not run {_names(gained)}", f"{subject} also runs {_names(gained)}{aw}"
        if lost:
            return 5, f"{subject} ran {_names(lost)}{bw}", f"{subject} no longer runs {_names(lost)}"
        return None
    if kind == "value" and b and a:
        key, old = _assignment(b)
        key2, new = _assignment(a)
        if key and key == key2:
            return 6, f"{_code(key)} was {_code(old)}{bw}", f"{_code(key)} is {_code(new)}{aw}"
        return None
    if kind == "condition" and b and a:
        return 7, f"{subject} branched on {_code(_cond(b))}", f"it branches on {_code(_cond(a))}"
    return None


# --- text helpers ------------------------------------------------------------------------


def _when(guard: str | None) -> str:
    return f" when {_code(guard)}" if guard else ""


def _expr(text: str) -> str:
    value = re.sub(r"^(?:return|yield)\b\s*", "", text).rstrip(";").strip() or "nothing"
    return value if len(value) <= 60 else value[:59] + "…"


def _cond(text: str) -> str:
    cleaned = re.sub(r"^(?:\}\s*)?(?:else\s+if|elif|if|while|for|switch|case|match|when|unless|guard)\b\s*", "", text)
    cleaned = cleaned.rstrip("{:").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1].strip()
    return cleaned if len(cleaned) <= 60 else cleaned[:59] + "…"


_ASSIGN = re.compile(r"^(?:(?:const|let|var|final|val)\s+)?(?P<lhs>[A-Za-z_][\w.]*)\s*(?::[^=]+)?(?::=|(?<![=!<>])=(?!=))\s*(?P<rhs>.+)$")
_KEYED = re.compile(r"^[\"']?(?P<lhs>[A-Za-z_][\w-]*)[\"']?\s*:\s*(?P<rhs>.+)$")


def _assignment(text: str) -> tuple[str | None, str]:
    match = _ASSIGN.match(text.rstrip(";,")) or _KEYED.match(text.rstrip(";,"))
    if not match:
        return None, ""
    value = match.group("rhs").strip()
    return match.group("lhs"), value if len(value) <= 60 else value[:59] + "…"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text.startswith("`") else text


def _names(names: list[str], limit: int = 3) -> str:
    shown = ", ".join(_code(name) for name in names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _code(text: str | None) -> str:
    if not text:
        return ""
    fence = "``" if "`" in text else "`"
    pad = " " if fence == "``" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out
