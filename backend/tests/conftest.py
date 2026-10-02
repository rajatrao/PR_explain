import os

os.environ["RUN_MIGRATIONS"] = "false"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_BASE_URL"] = "http://explain.example"
os.environ["LLM_PROVIDER"] = "ollama"
os.environ.pop("OLLAMA_BASE_URL", None)
os.environ.pop("OLLAMA_MODEL", None)

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.base import Base
from app.db.session import get_engine, reset_engine


@pytest.fixture
def db():
    get_settings.cache_clear()
    reset_engine()
    engine = get_engine()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        reset_engine()
        get_settings.cache_clear()
