from app.analyzer.types import Evidence, Relationship
from app.explanation.details import OUTSIDE_TITLE, build_details, render_details_markdown
from app.explanation.key_changes import _missing_required, _passes_param
from tests.test_behavior_comparison import _facts, _fn


def _with_outside_caller(snippet: str):
    symbols, relationships, claims, evidences = _facts()
    job = _fn("sym_job", "nightly_job", "app/jobs.py", 1, 5)
    symbols.append(job)
    evidences.append(
        Evidence(
            id="ev_call",
            type="call",
            repo="acme/shop",
            commit_sha="b" * 40,
            file="app/jobs.py",
            start_line=3,
            end_line=3,
            symbol="nightly_job",
            description="call",
            snippet=snippet,
        )
    )
    relationships.append(
        Relationship(
            id="r9",
            type="CALLS",
            source_id=job.id,
            target_id="sym_charge",
            source_name="nightly_job",
            target_name="charge",
            source_file="app/jobs.py",
            target_file="app/billing.py",
            evidence_id="ev_call",
        )
    )
    details = build_details(
        symbols=symbols, relationships=relationships, evidences=evidences, claims=claims, sections=[], repo="acme/shop", sha="b" * 40
    )
    return {section["title"]: section["rows"] for section in details["sections"]}, details


def test_key_changes_rank_removed_first_and_say_what_callers_see():
    rows, details = _with_outside_caller("    charge(order)")
    key = rows["Key Changes"]
    assert [row["label"] for row in key[:2]] == ["legacy_refund (removed)", "charge"]
    assert key[0]["value"].startswith("Removed by this pull request.")
    charge = key[1]["value"]
    assert charge.startswith("Changes its inputs, failures, result")
    assert "charge now takes currency" in charge
    assert "still call it and are not part of this diff" in charge
    assert charge.endswith("No test references it.")
    assert key[1]["label_href"].endswith("app/billing.py#L10-L24")
    markdown = render_details_markdown(details)
    assert "| Function | What it means for callers |" in markdown
    assert "### Change flow" not in markdown


def test_outside_callers_show_the_call_and_whether_it_still_fits():
    rows, _ = _with_outside_caller("    charge(order)")
    outside = {row["label"]: row for row in rows[OUTSIDE_TITLE]}
    job = outside["app/jobs.py"]["value"]
    assert job.startswith("nightly_job calls `charge(order)` at line 3.")
    assert "currency is optional, so this call gets the default." in job
    assert "The default of retries changes from 3 to 5, and this call relies on it." in job
    assert "Fails differently: ValueError becomes InvalidOrder." in job
    assert outside["app/jobs.py"]["label_href"].endswith("app/jobs.py#L3")


def test_positional_argument_is_not_reported_as_relying_on_the_default():
    rows, _ = _with_outside_caller("    charge(order, 2)")
    job = next(row for row in rows[OUTSIDE_TITLE] if row["label"] == "app/jobs.py")["value"]
    assert "relies on it" not in job


def test_missing_required_parameter_is_detected_by_position_and_keyword():
    header = "export function createSession(userId: string, ttlMs: number): string {"
    added = [("ttlMs", False)]
    assert _missing_required("createSession(userId)", header, added, "src/session.ts") == ["ttlMs"]
    assert _missing_required("createSession(userId, 3600)", header, added, "src/session.ts") == []
    assert _missing_required("createSession(...args)", header, added, "src/session.ts") == []
    assert _passes_param("charge(order, currency='EUR')", "def charge(order, retries=5, *, currency='USD'):", "currency", "a.py")
