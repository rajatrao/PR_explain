"""Load the oauth fixture into the local database so the explanation page has a run."""

from __future__ import annotations

from sqlalchemy import select

from app.analyzer.fixture import load_oauth_snapshot
from app.analyzer.types import FileChange, Snapshot
from app.config import get_settings
from app.db.migrate import upgrade
from app.db.models import AnalysisRun, GithubInstallation, PullRequest, Repository, Revision
from app.db.session import session_factory
from app.jobs.queue import enqueue_job
from app.llm.scripted import ScriptedProvider
from app.worker import process_available_job


class CountingSource:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def fetch(self, full_name: str, base_sha: str, head_sha: str):
        self.calls += 1
        self.snapshot.head_sha = head_sha
        self.snapshot.base_sha = base_sha
        self.snapshot.repository = full_name
        return self.snapshot


class MemoryComments:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def create_comment(self, full_name: str, pr_number: int, body: str) -> int:
        if self.fail:
            raise RuntimeError("github write failed")
        return 1

    def update_comment(self, full_name: str, comment_id: int, body: str) -> None:
        if self.fail:
            raise RuntimeError("github write failed")


def _run_for(session, snapshot: Snapshot) -> AnalysisRun:
    installation = session.get(GithubInstallation, 1)
    if installation is None:
        session.add(GithubInstallation(id=1, account_login="fixture"))
        session.add(Repository(id=2, installation_id=1, full_name=snapshot.repository))
        session.flush()
    pr = session.scalars(select(PullRequest).where(PullRequest.repository_id == 2)).first()
    if pr is None:
        pr = PullRequest(repository_id=2, number=snapshot.pr_number or 7)
        session.add(pr)
        session.flush()
    revision = Revision(
        pull_request_id=pr.id,
        head_sha=snapshot.head_sha,
        base_sha=snapshot.base_sha,
        title=snapshot.pr_title,
        body=snapshot.pr_body,
    )
    session.add(revision)
    session.flush()
    run = AnalysisRun(revision_id=revision.id, analysis_status="queued")
    session.add(run)
    session.flush()
    enqueue_job(session, run.id, "analyze")
    session.commit()
    return run


def main() -> None:
    upgrade()
    session = session_factory()()
    existing = session.scalars(select(AnalysisRun)).all()
    if existing:
        for run in existing:
            print(f"{run.id} explanation={run.explanation_status} sha={run.revision.head_sha}")
        return
    settings = get_settings()
    first = load_oauth_snapshot()
    source = CountingSource(first)
    failed = _run_for(session, first)
    process_available_job(session, settings, snapshot_source=source, provider=ScriptedProvider())
    process_available_job(
        session,
        settings,
        snapshot_source=source,
        provider=ScriptedProvider(error="Ollama is not configured"),
        comment_client=MemoryComments(),
    )
    second = Snapshot(
        repository=first.repository,
        base_sha=first.base_sha,
        head_sha="c" * 40,
        files=dict(first.files),
        changes=[
            *first.changes,
            FileChange(
                path="src/health.ts",
                status="modified",
                patch='@@ -1,3 +1,3 @@\n export function health(): string {\n-  return "ok";\n+  return "ready";\n }\n',
            ),
        ],
        pr_number=first.pr_number,
        pr_title="Health check now reports ready",
        pr_body=first.pr_body,
    )
    second.files["src/health.ts"] = 'export function health(): string {\n  return "ready";\n}\n'
    source.snapshot = second
    succeeded = _run_for(session, second)
    comments = MemoryComments(fail=True)
    process_available_job(session, settings, snapshot_source=source, provider=ScriptedProvider())
    process_available_job(
        session,
        settings,
        snapshot_source=source,
        provider=ScriptedProvider(),
        comment_client=comments,
    )
    session.expire_all()
    for run_id in (failed.id, succeeded.id):
        run = session.get(AnalysisRun, run_id)
        print(f"{run.id} explanation={run.explanation_status} comment={run.comment_status}")


if __name__ == "__main__":
    main()
