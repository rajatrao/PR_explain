from sqlalchemy.dialects import postgresql

from app.jobs.queue import lock_statement


def test_postgres_job_claim_uses_skip_locked():
    sql = str(lock_statement().compile(dialect=postgresql.dialect())).upper()
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql
