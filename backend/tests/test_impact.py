from app.analyzer.surface import find_surfaces
from app.analyzer.types import Claim, Evidence, FileChange
from app.analyzer.review_signals import _checks_result, _inside_try
from app.explanation.impact import build_impact, render_impact_markdown
from app.explanation.schema import BehaviorChangeFact, BehaviorFunctionFact


def _surface_patch():
    return [
        FileChange(
            "backend/app/api.py",
            "modified",
            '@@ -10,3 +10,4 @@\n-@app.get("/api/runs/{id}")\n+@app.get("/api/runs/{id}")\n+@app.post("/api/runs/{id}/retry")\n x\n',
        ),
        FileChange(
            "backend/alembic/versions/x.py",
            "added",
            '@@ -0,0 +1,4 @@\n+    op.add_column("analysis_runs", sa.Column("behavior", sa.JSON()))\n+    op.create_table(\n+        "behavior_facts",\n+    op.drop_column("runs", "old")\n',
        ),
        FileChange(".env.example", "modified", "@@ -1,2 +1,3 @@\n-LLM_MODEL=a\n+LLM_MODEL=b\n+LLM_TIMEOUT_MS=1000\n"),
        FileChange("backend/app/x.py", "modified", '@@ -1,1 +1,2 @@\n+    key = os.environ.get("API_KEY")\n'),
        FileChange("frontend/package.json", "modified", '@@ -1,2 +1,3 @@\n-    "mermaid": "^10.0.0",\n+    "mermaid": "^11.0.0",\n+    "zod": "^3.0.0",\n'),
        FileChange("frontend/src/RunPage.tsx", "modified", "@@ -1 +1 @@\n-a\n+b\n"),
    ]


def test_surfaces_are_read_from_both_sides_of_the_patch():
    found = {(f.level, f.kind, f.name) for f in find_surfaces(_surface_patch())}
    assert ("api", "added", "POST /api/runs/{id}/retry") in found
    # The same route line on both sides is a move, not a change.
    assert not any(name == "GET /api/runs/{id}" for _l, _k, name in found)
    assert ("data", "added", "column analysis_runs.behavior") in found
    assert ("data", "added", "table behavior_facts") in found
    assert ("data", "removed", "column runs.old") in found
    assert ("config", "changed", "LLM_MODEL") in found
    assert ("config", "added", "LLM_TIMEOUT_MS") in found
    assert ("config", "added", "API_KEY") in found
    assert ("dependency", "changed", "mermaid") in found
    assert ("dependency", "added", "zod") in found
    assert ("ui", "changed", "frontend/src/RunPage.tsx") in found


def _claims_and_evidence():
    claims, evidences = [], []
    for index, finding in enumerate(find_surfaces(_surface_patch())):
        evidences.append(
            Evidence(
                id=f"e{index}",
                type="diff_hunk",
                repo="r",
                commit_sha="b",
                file=finding.file_path,
                start_line=finding.line,
                end_line=finding.line,
                symbol=None,
                description=f"{finding.level}|{finding.kind}|{finding.name}|{finding.detail}",
            )
        )
        claims.append(Claim(id=f"c{index}", epistemic="FACT", kind="surface_changed", text="", subject=finding.name, evidence_ids=[f"e{index}"]))
    return claims, evidences


def _context(claims, evidences, caller, callee, handled, checks=""):
    eid = f"ctx-{caller}"
    evidences.append(
        Evidence(id=eid, type="source_span", repo="r", commit_sha="b", file="x", start_line=1, end_line=1, symbol=callee,
                 description=f"context|{caller}|{callee}|{'handled' if handled else 'unhandled'}|{checks}")
    )
    claims.append(Claim(id=f"cl-{eid}", epistemic="FACT", kind="call_context", text="", subject=callee, evidence_ids=[eid]))


def _charge():
    return BehaviorFunctionFact(
        function="charge",
        file="app/billing.py",
        public=True,
        reached_from=["post_checkout"],
        callers_at_head=["checkout → charge(cart.order)"],
        changes=[
            BehaviorChangeFact(id="b1", claim_id="x", kind="error", before='raise ValueError("empty")', after='raise InvalidOrder("negative")', before_when="total <= 0", after_when="total < 0"),
            BehaviorChangeFact(id="b2", claim_id="y", kind="return", after="return None", after_when="total == 0"),
        ],
    )


def _session():
    return BehaviorFunctionFact(
        function="createSession",
        file="src/session.ts",
        public=True,
        reached_from=["login", "googleCallback", "refreshToken"],
        callers_at_head=["login → createSession(userId, 3600)", "googleCallback → createSession(userId, 3600)", "refreshToken → createSession(userId, 7200)"],
        notes=["All 3 head call sites of createSession pass a matching number of arguments."],
        changes=[
            BehaviorChangeFact(
                id="b3",
                claim_id="z",
                kind="signature",
                before="export function createSession(userId: string): string {",
                after="export function createSession(userId: string, ttlMs: number): string {",
            )
        ],
    )


def test_impact_ranks_findings_by_rule_and_lists_dependents():
    claims, evidences = _claims_and_evidence()
    _context(claims, evidences, "checkout", "charge", handled=False, checks="if receipt is None")
    claims.append(Claim(id="s1", epistemic="FACT", kind="stale_test", text="tests/test_cart.py exercises charge, which changed, and the test is not changed in this pull request.", subject="charge"))
    claims.append(Claim(id="k1", epistemic="FACT", kind="config_undocumented", text="", subject="API_KEY"))
    claims.append(Claim(id="v1", epistemic="FACT", kind="resolution_coverage", text="", subject="coverage:12/20"))
    claims.append(Claim(id="f1", epistemic="FACT", kind="file_changed", text="", subject="app/billing.py"))
    section = build_impact(claims=claims, evidences=evidences, behavior_facts=[_charge(), _session()])

    titles = [(item["severity"], item["title"]) for item in section["attention"]]
    assert titles == [
        ("high", "A migration drops the column runs.old"),
        ("high", "New failure can reach post_checkout"),
        ("medium", "Shared function createSession changes its contract"),
    ]
    failure = section["attention"][1]["why"]
    assert failure == (
        "charge now fails with InvalidOrder when `total < 0` (it used to fail with ValueError). "
        "No caller wraps the call in a try block: checkout."
    )
    assert section["attention"][2]["why"].startswith("Callers must now pass `ttlMs`. Called from login, googleCallback, refreshToken.")

    assert [d["entry"] for d in section["dependents"]][:2] == ["googleCallback", "login"]
    login = next(d for d in section["dependents"] if d["entry"] == "login")
    assert login == {"entry": "login", "reaches": ["createSession"], "calls": ["createSession(userId, 3600)"]}

    assert section["verify"][0].startswith("`tests/test_cart.py` exercises charge but was not updated")
    assert "Set `API_KEY` in every environment before deploying." in section["verify"]
    assert section["not_affected"] == ""
    assert section["partial"] == "8 of 20 calls in the changed files could not be resolved to one function, so other consumers may exist."
    assert section["scope"].startswith("Several workflows. 1 production file changed; the changed behavior is reached from 4 entry points")

    markdown = render_impact_markdown(section)
    assert markdown.startswith("### Impact\n\n**Scope:** Several workflows.")
    assert "1. **High: A migration drops the column runs.old.** Data stored in it is lost when the migration runs." in markdown
    assert "- login — reaches createSession via `createSession(userId, 3600)`" in markdown
    assert "- [ ] Set `API_KEY` in every environment before deploying." in markdown
    for noise in ("production files have no", "truncated", "test count"):
        assert noise not in markdown


def test_handled_failure_is_medium_and_complete_coverage_says_not_affected():
    claims, evidences = [], []
    _context(claims, evidences, "checkout", "charge", handled=True)
    claims.append(Claim(id="v1", epistemic="FACT", kind="resolution_coverage", text="", subject="coverage:19/20"))
    section = build_impact(claims=claims, evidences=evidences, behavior_facts=[_charge()])
    failure = next(item for item in section["attention"] if item["title"].startswith("New failure"))
    assert failure["severity"] == "medium"
    assert failure["why"].endswith("Handled by checkout.")
    early = next(item for item in section["attention"] if item["title"].startswith("Callers of charge"))
    assert early["title"] == "Callers of charge can now receive `None`"
    assert section["not_affected"].startswith("Every call in the changed files resolved")
    assert section["partial"] == ""


def test_try_and_result_checks_are_read_from_caller_lines():
    py = [
        "def checkout(cart):",
        "    try:",
        "        receipt = charge(cart.order)",
        "        if receipt is None:",
        "            return 'free'",
        "    except InvalidOrder:",
        "        return 'rejected'",
        "    other = charge(cart.extra)",
    ]
    assert _inside_try(py, 3, 1, "app/cart.py") is True
    assert _inside_try(py, 8, 1, "app/cart.py") is False
    assert _checks_result(py, 3) == "if receipt is None"
    ts = [
        "export function login(userId: string) {",
        "  try {",
        "    const s = createSession(userId, 3600);",
        "    if (!s) { return null; }",
        "  } catch (e) { return null; }",
        "  return createSession(userId, 1);",
        "}",
    ]
    assert _inside_try(ts, 3, 1, "src/login.ts") is True
    assert _inside_try(ts, 6, 1, "src/login.ts") is False
    assert _checks_result(ts, 3) == "if (!s"
