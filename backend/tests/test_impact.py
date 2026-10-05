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
            function="retry_run",
            file="backend/app/api.py",
            public=True,
            reached_from=["retry_run"],
            changes=[BehaviorChangeFact(id="b1", claim_id="x", kind="error", after='raise HTTPException(status_code=409, detail="run is not failed")', after_when="run.analysis_status != 'failed'")],
        )
    ]


def test_impact_facts_by_level_without_file_paths():
    claims, evidences = _claims_and_evidence()
    facts = build_impact_facts(claims=claims, evidences=evidences, behavior_facts=_behavior())
    by_level = {}
    for fact in facts:
        by_level.setdefault(fact.level, []).append(fact)
    assert [f.text for f in by_level["api"]] == ["HTTP route POST /api/runs/{id}/retry is added."]
    assert by_level["api"][0].behavior_ids == ["b1"]
    assert "Database column analysis_runs.behavior is added (column)." in [f.text for f in by_level["data"]]
    assert "The migration drops column runs.old." in [f.text for f in by_level["data"]]
    assert "Package mermaid changes (^10.0.0 → ^11.0.0)." in [f.text for f in by_level["dependency"]]
    assert by_level["ui"][0].text == "The web interface changes: 1 changed interface file."
    assert by_level["system"][0].text == "Changed behavior is reachable from 1 public entry point: retry_run."
    assert not any("/" in f.text and ".py" in f.text for f in facts)

    section = build_impact_section(narrative=None, behavior_facts=_behavior(), impact_facts=facts)
    assert section["source"] == "facts"
    assert [level["label"] for level in section["levels"]][:3] == ["System", "API and contracts", "Data"]
    markdown = render_impact_markdown(section)
    assert markdown.startswith("### Impact")
    assert "**API and contracts** — HTTP route POST /api/runs/{id}/retry is added." in markdown


def test_model_impact_is_screened_per_level():
    claims, evidences = _claims_and_evidence()
    facts = build_impact_facts(claims=claims, evidences=evidences, behavior_facts=_behavior())
    api_id = next(f.id for f in facts if f.level == "api")
    data_ids = [f.id for f in facts if f.level == "data"]
    narrative = ImpactNarrative.model_validate(
        {
            "levels": [
                {
                    "level": "api",
                    "summary": "Clients gain a `POST /api/runs/{id}/retry` endpoint that answers 409 when the run has not failed.",
                    "details": ["Calling it through `retry_run()` is the only way in."],
                    "fact_ids": [api_id, "b1"],
                },
                {
                    "level": "data",
                    "summary": "Each run stores a new `behavior` column, and the old `runs.old` column is dropped, so its values are lost.",
                    "fact_ids": data_ids,
                },
                {"level": "data", "summary": "Duplicate level.", "fact_ids": data_ids},
                {"level": "security", "summary": "Tokens are safer.", "fact_ids": [api_id]},
                {"level": "config", "summary": "Operators must set `REDIS_URL`.", "fact_ids": [api_id]},
                {"level": "ui", "summary": "The page now renders in backend/app/api.py.", "fact_ids": [api_id]},
            ]
        }
    )
    reasons: list[str] = []
    kept = screen_impact(narrative, _behavior(), facts, reasons)
    assert [level.level for level in kept.levels] == ["api", "data"]
    # A detail that writes call syntax is dropped; the level's summary stays.
    assert kept.levels[0].details == []
    assert any("REDIS_URL" in reason for reason in reasons)
    assert any("named a file" in reason for reason in reasons)
    section = build_impact_section(narrative=kept, behavior_facts=_behavior(), impact_facts=facts, prescreened=True)
    assert section["source"] == "model"
    assert "Written by the configured model" in render_impact_markdown(section)
