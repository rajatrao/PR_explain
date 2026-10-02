from pathlib import Path

from app.analyzer.analyze import analyze
from app.analyzer.fixture import load_oauth_snapshot
from app.analyzer.types import FileChange, Snapshot


def test_create_session_callers_tests_and_password_boundary():
    result = analyze(load_oauth_snapshot())
    calls = [
        rel
        for rel in result.relationships
        if rel.type == "CALLS" and rel.target_name == "createSession"
    ]
    assert {rel.source_name for rel in calls} == {"login", "googleCallback", "refreshToken"}

    tests = [rel for rel in result.relationships if rel.type == "TESTS"]
    assert {rel.source_file for rel in tests} == {
        "src/login.test.ts",
        "src/oauth.test.ts",
    }
    assert all(rel.target_name != "refreshToken" for rel in tests)
    assert {rel.target_name for rel in tests} == {"login", "googleCallback"}

    call_claims = [claim for claim in result.claims if claim.kind == "calls"]
    evidence = {item.id: item for item in result.evidences}
    files = load_oauth_snapshot().files
    for claim in call_claims:
        assert claim.epistemic == "FACT"
        assert claim.evidence_ids
        for evidence_id in claim.evidence_ids:
            item = evidence[evidence_id]
            assert item.file
            assert item.start_line
            line = files[item.file].splitlines()[item.start_line - 1]
            assert "createSession(" in line

    assert any(claim.kind == "missing_test" and claim.subject == "refreshToken" for claim in result.claims)
    assert not any(
        claim.kind == "behavior_unchanged" and "password" in claim.text
        for claim in result.claims
    )
    assert any(
        claim.kind == "reaches_changed" and "src/password.ts" in claim.text and "not unchanged" in claim.text
        for claim in result.claims
    )
    assert any(
        claim.kind == "file_absent" and claim.subject == "src/password.ts" for claim in result.claims
    )
    assert any(
        claim.kind == "behavior_unchanged" and claim.subject == "src/health.ts" for claim in result.claims
    )
    assert result.language_coverage == "ts"


def test_ambiguous_name_does_not_guess_an_edge():
    snapshot = Snapshot(
        repository="fixture/ambiguous",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files={
            "src/a.ts": "export function run(): string { return 'a'; }\n",
            "src/b.ts": "export function run(): string { return 'b'; }\n",
            "src/c.ts": "export function start(): string { return run(); }\n",
            "tsconfig.json": "{}\n",
        },
        changes=[
            FileChange(
                path="src/c.ts",
                status="modified",
                patch="@@ -1 +1 @@\n-export function start(): string { return 'x'; }\n+export function start(): string { return run(); }\n",
            )
        ],
    )
    result = analyze(snapshot)
    assert not any(rel.type == "CALLS" and rel.target_name == "run" for rel in result.relationships)
    assert any(claim.kind == "ambiguous_call" and claim.epistemic == "UNKNOWN" for claim in result.claims)


def test_fanout_cap_records_omitted_callers():
    callers = "\n".join(
        f"export function caller{i}(): string {{ return core(); }}" for i in range(3)
    )
    snapshot = Snapshot(
        repository="fixture/fanout",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files={
            "src/core.ts": "export function core(): string { return 'core'; }\n",
            "src/callers.ts": callers + "\n",
            "tsconfig.json": "{}\n",
        },
        changes=[
            FileChange(
                path="src/core.ts",
                status="modified",
                patch="@@ -1 +1 @@\n-export function core(): string { return 'old'; }\n+export function core(): string { return 'core'; }\n",
            )
        ],
    )
    result = analyze(snapshot, fanout_cap=2)
    kept = [rel for rel in result.relationships if rel.type == "CALLS" and rel.target_name == "core"]
    assert len(kept) == 2
    assert any(claim.kind == "fanout_truncated" and "1 further callers" in claim.text for claim in result.claims)


def test_diff_only_when_no_typescript():
    snapshot = Snapshot(
        repository="fixture/docs",
        base_sha="a" * 40,
        head_sha="c" * 40,
        files={"README.md": "# notes\n"},
        changes=[
            FileChange(
                path="README.md",
                status="modified",
                patch="@@ -1 +1 @@\n-# old\n+# notes\n",
            )
        ],
    )
    result = analyze(snapshot)
    assert result.language_coverage == "diff_only"
    assert any(claim.kind == "diff_only" and claim.epistemic == "UNKNOWN" for claim in result.claims)
    assert not any(rel.type == "CALLS" for rel in result.relationships)


def test_dependency_manifest_does_not_invent_calls():
    snapshot = Snapshot(
        repository="fixture/deps",
        base_sha="a" * 40,
        head_sha="d" * 40,
        files={"package.json": '{"dependencies": {"left-pad": "1.3.0"}}\n'},
        changes=[
            FileChange(
                path="package.json",
                status="modified",
                patch='@@ -1 +1 @@\n-{"dependencies": {}}\n+{"dependencies": {"left-pad": "1.3.0"}}\n',
            )
        ],
    )
    result = analyze(snapshot)
    assert any(claim.kind == "dependency_changed" for claim in result.claims)
    assert any(rel.type == "DEPENDS_ON" and rel.target_name == "left-pad" for rel in result.relationships)
    assert not any(rel.type == "CALLS" for rel in result.relationships)


def test_fixture_paths_exist():
    root = Path(__file__).resolve().parents[2] / "fixtures" / "oauth-ts" / "head"
    assert (root / "src" / "session.ts").is_file()
