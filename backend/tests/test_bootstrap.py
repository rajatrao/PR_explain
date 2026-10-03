import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import app
from app.bootstrap import GithubAppCatalog, sync_installed_repositories
from app.config import Settings
from app.db.models import AnalysisRun, GithubInstallation, Repository, Revision
from tests.test_github_auth import _throwaway_pem
from tests.test_webhook import _signed


class FakeCatalog:
    def __init__(self) -> None:
        self.installation_ids: list[int] = []

    def list_installations(self) -> list[dict]:
        return [
            {
                "id": 167345129,
                "account": {"login": "rajatrao"},
                "app_slug": "explain-pr",
            }
        ]

    def list_repositories(self, installation_id: int) -> list[dict]:
        self.installation_ids.append(installation_id)
        return [
            {
                "id": 555,
                "name": "PR_explain",
                "full_name": "rajatrao/PR_explain",
                "owner": {"login": "rajatrao"},
                "default_branch": "main",
            }
        ]


def test_sync_upserts_installation_and_repository_from_fake_catalog(db):
    catalog = FakeCatalog()
    first = sync_installed_repositories(db, catalog)
    second = sync_installed_repositories(db, catalog)

    assert catalog.installation_ids == [167345129, 167345129]
    assert first["repositories"] == 1
    assert second["repositories"] == 1
    assert db.scalar(select(func.count()).select_from(Repository)) == 1
    stored = db.get(Repository, 555)
    assert stored is not None
    assert stored.full_name == "rajatrao/PR_explain"
    assert stored.full_name.split("/", 1)[0] == "rajatrao"
    assert stored.installation_id == 167345129
    assert stored.default_branch == "main"
    installation = db.get(GithubInstallation, 167345129)
    assert installation is not None
    assert installation.account_login == "rajatrao"


def test_pull_request_payload_upserts_repository_and_second_delivery_reuses_it(db):
    client = TestClient(app)
    opened = {
        "action": "opened",
        "installation": {"id": 167345129, "account": {"login": "rajatrao"}},
        "repository": {
            "id": 555,
            "name": "PR_explain",
            "owner": {"login": "rajatrao"},
            "default_branch": "main",
        },
        "pull_request": {
            "number": 8,
            "title": "Bootstrap",
            "head": {"sha": "b" * 40},
            "base": {"sha": "a" * 40},
        },
    }
    body, headers = _signed(opened, "pull_request", "delivery-bootstrap-1")
    first = client.post("/api/webhooks/github", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "queued"

    db.expire_all()
    stored = db.get(Repository, 555)
    assert stored is not None
    assert stored.full_name == "rajatrao/PR_explain"
    assert stored.installation_id == 167345129

    again = {
        "action": "synchronize",
        "installation": {"id": 167345129, "account": {"login": "rajatrao"}},
        "repository": {
            "name": "PR_explain",
            "full_name": "rajatrao/PR_explain",
            "owner": {"login": "rajatrao"},
            "default_branch": "main",
        },
        "pull_request": {
            "number": 8,
            "title": "Bootstrap",
            "head": {"sha": "c" * 40},
            "base": {"sha": "a" * 40},
        },
    }
    body, headers = _signed(again, "pull_request", "delivery-bootstrap-2")
    second = client.post("/api/webhooks/github", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["status"] == "queued"

    db.expire_all()
    rows = db.scalars(select(Repository)).all()
    assert len(rows) == 1
    assert rows[0].id == stored.id
    assert rows[0].full_name == "rajatrao/PR_explain"
    runs = db.scalars(select(AnalysisRun)).all()
    assert len(runs) == 2
    for run in runs:
        revision = db.get(Revision, run.revision_id)
        assert revision is not None
        assert revision.pull_request.repository_id == stored.id


def test_catalog_lists_pages_and_hides_credentials():
    pem, _public = _throwaway_pem()
    settings = Settings(
        github_app_id="4242",
        github_app_private_key=pem,
        github_api_url="https://api.github.com",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/app/installations":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json=[{"id": 2, "account": {"login": "other"}}])
            return httpx.Response(
                200,
                json=[{"id": 167345129, "account": {"login": "rajatrao"}}],
                headers={
                    "Link": '<https://api.github.com/app/installations?per_page=100&page=2>; rel="next"'
                },
            )
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_fake"})
        if request.url.path == "/installation/repositories":
            if request.headers.get("Authorization") != "Bearer ghs_fake":
                return httpx.Response(401, json={"message": "Bad credentials"})
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {
                            "id": 555,
                            "name": "PR_explain",
                            "full_name": "rajatrao/PR_explain",
                            "owner": {"login": "rajatrao"},
                            "default_branch": "main",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"message": "missing"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    catalog = GithubAppCatalog(settings, http=http)
    try:
        installations = catalog.list_installations()
        repositories = catalog.list_repositories(167345129)
    finally:
        catalog.close()

    assert [item["id"] for item in installations] == [167345129, 2]
    assert repositories[0]["full_name"] == "rajatrao/PR_explain"

    def deny(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"token": "ghs_leaked", "message": "Bad credentials"})

    denied = GithubAppCatalog(settings, http=httpx.Client(transport=httpx.MockTransport(deny)))
    try:
        try:
            denied.list_installations()
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected the catalog call to fail")
    finally:
        denied.close()
    assert "401" in message
    assert "Bearer" not in message
    assert "ghs_leaked" not in message
    assert "BEGIN" not in message
