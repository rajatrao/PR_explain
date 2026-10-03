from app.llm.scripted import ScriptedProvider, document_from_packet

__all__ = ["CountingSource", "MemoryComments", "ScriptedProvider", "document_from_packet"]


class MemoryComments:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.bodies: list[str] = []
        self.comments: dict[int, str] = {}
        self._next = 1

    def list_comments(self, full_name: str, pr_number: int) -> list[dict]:
        if self.fail:
            raise RuntimeError("github write failed")
        return [{"id": comment_id, "body": body} for comment_id, body in self.comments.items()]

    def create_comment(self, full_name: str, pr_number: int, body: str) -> int:
        if self.fail:
            raise RuntimeError("github write failed")
        comment_id = self._next
        self._next += 1
        self.comments[comment_id] = body
        self.bodies.append(body)
        return comment_id

    def update_comment(self, full_name: str, comment_id: int, body: str) -> None:
        if self.fail:
            raise RuntimeError("github write failed")
        if comment_id not in self.comments:
            from app.github.client import GitHubNotFound

            raise GitHubNotFound(str(comment_id))
        self.comments[comment_id] = body
        self.bodies.append(body)


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
