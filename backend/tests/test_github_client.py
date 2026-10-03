import httpx

from app.github.client import GitHubClient


def test_tarball_url_includes_owner_and_repo():
    owner = "acme"
    repo = "app"
    sha = "abc123def"
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"tarball")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = GitHubClient(token="test-token", api_url="https://api.github.com", http=http)
    try:
        blob = client.download_tarball(f"{owner}/{repo}", sha)
    finally:
        client.close()

    assert blob == b"tarball"
    assert seen["url"] == f"https://api.github.com/repos/{owner}/{repo}/tarball/{sha}"


def test_tarball_redirect_drops_authorization_on_codeload():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://codeload.github.com/acme/app/legacy.tar.gz/abc123def?token=redirect-token"
                },
            )
        return httpx.Response(200, content=b"tarball-bytes")

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    client = GitHubClient(token="test-token", api_url="https://api.github.com", http=http)
    try:
        blob = client.download_tarball("acme/app", "abc123def")
    finally:
        client.close()

    assert blob == b"tarball-bytes"
    assert len(seen) == 2
    assert seen[0].url.host == "api.github.com"
    assert seen[0].headers["Authorization"] == "Bearer test-token"
    assert seen[1].url.host == "codeload.github.com"
    assert "authorization" not in seen[1].headers
