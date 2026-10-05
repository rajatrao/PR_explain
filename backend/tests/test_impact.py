from app.analyzer.surface import find_surfaces
from app.analyzer.types import Claim, Evidence, FileChange
from app.explanation.impact import build_impact_section, render_impact_markdown, screen_impact
from app.explanation.impact_facts import build_impact_facts
from app.explanation.schema import BehaviorChangeFact, BehaviorFunctionFact, ImpactNarrative


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


def _behavior():
    return [
        BehaviorFunctionFact(
            function="createSession",
            file="src/session.ts",
            public=True,
            reached_from=["login", "googleCallback"],
            notes=["createSession now declares 2 required parameters; login passes 1 argument."],
            changes=[
                BehaviorChangeFact(
                    id="b1",
                    claim_id="x",
                    kind="signature",
                    before="export function createSession(userId: string): string {",
                    after="export function createSession(userId: string, ttlMs: number): string {",
                )
            ],
        )
    ]


def test_impact_facts_are_areas_and_risks_without_analysis_bookkeeping():
    claims, evidences = _claims_and_evidence()
    claims.append(Claim(id="r1", epistemic="FACT", kind="reaches_changed", text="x reaches y", subject="src/a.ts"))
    claims.append(Claim(id="u1", epistemic="INFERENCE", kind="behavior_unchanged", text="unchanged", subject="src/b.ts"))
    claims.append(Claim(id="t1", epistemic="UNKNOWN", kind="fanout_truncated", text="12 further callers omitted.", subject="f"))
    claims.append(Claim(id="m1", epistemic="UNKNOWN", kind="missing_test", text="No test references f.", subject="f"))
    facts = build_impact_facts(claims=claims, evidences=evidences, behavior_facts=_behavior())
    areas = {(f.area, f.text) for f in facts if f.kind == "area"}
    risks = {(f.severity, f.text) for f in facts if f.kind == "risk"}
    assert ("Affected flows", "Changed behavior is reachable from public entry points login, googleCallback.") in areas
    assert ("Public API", "Route POST /api/runs/{id}/retry is added.") in areas
    assert ("Data", "The table behavior_facts is dropped.") not in areas
    assert ("high", "A migration drops the column runs.old; data stored in it is lost when the migration runs.") in risks
    assert ("high", "A caller may break: createSession now declares 2 required parameters; login passes 1 argument.") in risks
    assert ("medium", "Callers of createSession must now pass ttlMs.") in risks
    assert ("medium", "Package mermaid crosses a major version (^10.0.0 to ^11.0.0).") in risks
    assert ("medium", "New setting API_KEY: every environment must provide it or rely on a default.") in risks
    blob = " ".join(f.text for f in facts)
    for noise in ("production file", "call sites were", "truncated", "test", "Testing", ".py", ".ts"):
        assert noise not in blob, noise

    section = build_impact_section(narrative=None, behavior_facts=_behavior(), impact_facts=facts)
    assert section["source"] == "facts"
    assert [risk["severity"] for risk in section["risks"]][:2] == ["high", "high"]
    markdown = render_impact_markdown(section)
    assert "**Impact areas**" in markdown and "**Risks**" in markdown
    assert "- **High** — A migration drops the column runs.old" in markdown


def test_model_impact_is_screened_and_severity_is_capped_by_the_facts():
    claims, evidences = _claims_and_evidence()
    facts = build_impact_facts(claims=claims, evidences=evidences, behavior_facts=_behavior())
    fid = {f.text: f.id for f in facts}
    api_area = fid["Route POST /api/runs/{id}/retry is added."]
    major = fid["Package mermaid crosses a major version (^10.0.0 to ^11.0.0)."]
    drop = fid["A migration drops the column runs.old; data stored in it is lost when the migration runs."]
    narrative = ImpactNarrative.model_validate(
        {
            "areas": [
                {"area": "Run retries", "summary": "Clients can retry a failed run through `POST /api/runs/{id}/retry`.", "fact_ids": [api_area]},
                {"area": "Sessions", "summary": "Sessions now go through `createSession()`.", "fact_ids": [api_area]},
                {"area": "Caching", "summary": "Results are cached in `REDIS_URL`.", "fact_ids": [api_area]},
            ],
            "risks": [
                {"severity": "high", "risk": "The diagram renderer moves to a new major version and may render differently.", "fact_ids": [major]},
                {"severity": "medium", "risk": "Existing values in runs.old are lost on upgrade.", "fact_ids": [drop]},
                {"severity": "high", "risk": "The new route has no auth.", "fact_ids": [api_area]},
            ],
        }
    )
    reasons: list[str] = []
    kept = screen_impact(narrative, _behavior(), facts, reasons)
    assert [area.area for area in kept.areas] == ["Run retries"]
    # Severity cannot exceed the cited risk fact (medium); a lower severity is kept as written.
    assert [(risk.severity, risk.risk[:24]) for risk in kept.risks] == [
        ("medium", "The diagram renderer mov"),
        ("medium", "Existing values in runs."),
    ]
    assert any("cites no risk fact" in reason for reason in reasons)
    section = build_impact_section(narrative=kept, behavior_facts=_behavior(), impact_facts=facts, prescreened=True)
    assert section["source"] == "model"
    assert "Written by the configured model" in render_impact_markdown(section)
