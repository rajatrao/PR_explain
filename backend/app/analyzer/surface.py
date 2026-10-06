"""Externally visible surfaces a pull request touches, read from the compare patch.

Each finding names something a user, client, operator, or downstream system can
see or depend on, and records which side of the diff it is on:

* ``api``     HTTP routes declared with a method decorator or router call,
* ``data``    tables and columns in migrations, SQL, and ORM models,
* ``config``  environment variables and settings fields,
* ``dependency`` packages in manifests,
* ``ui``      frontend files (the web interface).

Only ``+`` and ``-`` lines are read. A name that appears on both sides of one
file is reported as changed, not as added and removed. Nothing is inferred
beyond the matched line.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from app.analyzer.types import FileChange

_ROUTE_DECORATOR = re.compile(
    r"@\w+(?:\.\w+)*\.(get|post|put|patch|delete|head|options|api_route|route)\(\s*[\"']([^\"']+)[\"']", re.IGNORECASE
)
_ROUTE_CALL = re.compile(r"\b(?:app|router|server|api)\.(get|post|put|patch|delete)\(\s*[\"'`](/[^\"'`]*)[\"'`]")
_JAVA_ROUTE = re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?[\"']([^\"']+)[\"']")
_GO_ROUTE = re.compile(r"\.(GET|POST|PUT|PATCH|DELETE|HandleFunc|Handle)\(\s*\"(/[^\"]*)\"")

_CREATE_TABLE = re.compile(r"(?:op\.create_table|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s*\(?\s*[\"'`]?([A-Za-z_][\w.]*)", re.IGNORECASE)
_DROP_TABLE = re.compile(r"(?:op\.drop_table|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\s*\(?\s*[\"'`]?([A-Za-z_][\w.]*)", re.IGNORECASE)
_ADD_COLUMN = re.compile(r"op\.add_column\(\s*[\"']([\w.]+)[\"']\s*,\s*sa\.Column\(\s*[\"'](\w+)[\"']")
_DROP_COLUMN = re.compile(r"op\.drop_column\(\s*[\"']([\w.]+)[\"']\s*,\s*[\"'](\w+)[\"']")
_ALTER_COLUMN = re.compile(r"op\.alter_column\(\s*[\"']([\w.]+)[\"']\s*,\s*[\"'](\w+)[\"']")
_SQL_ADD_COLUMN = re.compile(r"ALTER\s+TABLE\s+[\"'`]?([\w.]+)[\"'`]?\s+ADD\s+(?:COLUMN\s+)?[\"'`]?(\w+)", re.IGNORECASE)
_SQL_DROP_COLUMN = re.compile(r"ALTER\s+TABLE\s+[\"'`]?([\w.]+)[\"'`]?\s+DROP\s+(?:COLUMN\s+)?[\"'`]?(\w+)", re.IGNORECASE)
_TABLENAME = re.compile(r"__tablename__\s*=\s*[\"'](\w+)[\"']")
_MODEL_FIELD = re.compile(r"^\s*(\w+)\s*(?::\s*Mapped\[[^\]]*\]\s*)?=\s*(?:mapped_column|Column|db\.Column|models\.\w+Field)\(")
_SA_COLUMN = re.compile(r"sa\.Column\(\s*[\"'](\w+)[\"']")

_ENV_READ = re.compile(
    r"(?:os\.environ(?:\.get)?\s*[\[(]\s*|os\.getenv\(\s*|getenv\(\s*|System\.getenv\(\s*)[\"']([A-Z][A-Z0-9_]+)[\"']"
    r"|process\.env\.([A-Z][A-Z0-9_]+)|import\.meta\.env\.([A-Z][A-Z0-9_]+)"
)
_ENV_FILE_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]+)\s*=")
_COMPOSE_ENV = re.compile(r"^\s*-?\s*([A-Z][A-Z0-9_]+)\s*[:=]")
_SETTINGS_FIELD = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*:\s*[\w\[\]|., ]+?(?:\s*=.*)?$")

_PACKAGE_JSON_DEP = re.compile(r"^\s*\"(@?[\w./-]+)\"\s*:\s*\"([^\"]+)\"")
_PYPROJECT_DEP = re.compile(r"^\s*\"([A-Za-z0-9][\w.\-\[\]]*)\s*([<>=!~^][^\"]*)?\"\s*,?\s*$")
_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][\w.\-\[\]]*)\s*([<>=!~][^#\s]*)?\s*(?:#.*)?$")
_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([\w.\-]+/[\w./\-]+)\s+(v[\w.\-+]+)")

_FRONTEND_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html")
_MANIFESTS = {"package.json", "pyproject.toml", "requirements.txt", "go.mod", "pom.xml", "build.gradle", "cargo.toml", "gemfile"}
_PACKAGE_META = {"name", "version", "private", "description", "type", "main", "module", "license", "scripts", "author"}


@dataclass
class SurfaceFinding:
    """A system surface the diff touches: an HTTP route, stored data, configuration, a dependency, or a web interface file, with whether it was added, removed, or changed and where."""

    level: str  # api | data | config | dependency | ui
    kind: str  # added | removed | changed
    name: str
    detail: str
    file_path: str
    line: int | None
    snippet: str


def find_surfaces(changes: list[FileChange], *, skip_path=None) -> list[SurfaceFinding]:
    findings: list[SurfaceFinding] = []
    ui_files: list[FileChange] = []
    for change in changes:
        if skip_path and skip_path(change.path):
            continue
        path = change.path
        base = posixpath.basename(path).lower()
        if change.patch is None:
            continue
        sides = _sides(change.patch)
        file_findings: list[tuple[str, str, str, str, int | None, str]] = []  # level, side, name, detail, line, text
        pending: dict[str, str] = {}
        for side, line_no, text in sides:
            for level, name, detail in _scan_line(path, base, text):
                file_findings.append((level, side, name, detail, line_no, text))
            # `op.create_table(` with the table name on the next line of the same side.
            if side in pending:
                match = re.match(r"^\s*[\"'](\w+)[\"']", text)
                if match:
                    detail = "table dropped by a migration" if pending[side] == "drop" else "table"
                    file_findings.append(("data", side, f"table {match.group(1)}", detail, line_no, text))
                pending.pop(side, None)
            opener = re.search(r"op\.(create|drop)_table\(\s*$", text)
            if opener:
                pending[side] = opener.group(1)
        findings.extend(_merge_sides(path, file_findings))
        if base.endswith(_FRONTEND_SUFFIXES) or path.startswith("frontend/") and base.endswith((".ts", ".js")):
            ui_files.append(change)
    for change in ui_files:
        findings.append(
            SurfaceFinding(
                level="ui",
                kind="removed" if change.status == "removed" else "added" if change.status == "added" else "changed",
                name=change.path,
                detail="web interface file",
                file_path=change.path,
                line=None,
                snippet="",
            )
        )
    return findings


def _sides(patch: str) -> list[tuple[str, int | None, str]]:
    out: list[tuple[str, int | None, str]] = []
    old_no = new_no = None
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            old_no, new_no = (int(match.group(1)), int(match.group(2))) if match else (None, None)
            continue
        if old_no is None or raw.startswith(("+++", "---", "\\")):
            continue
        if raw.startswith("-"):
            out.append(("removed", old_no, raw[1:]))
            old_no += 1
        elif raw.startswith("+"):
            out.append(("added", new_no, raw[1:]))
            new_no += 1
        else:
            old_no += 1
            new_no += 1
    return out


def _scan_line(path: str, base: str, text: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    stripped = text.strip()
    if not stripped or stripped.startswith(("#", "//", "*", "/*")) and not base.startswith(".env"):
        return found
    lowered = path.lower()

    for regex in (_ROUTE_DECORATOR, _ROUTE_CALL, _JAVA_ROUTE, _GO_ROUTE):
        for match in regex.finditer(text):
            method, route = match.group(1).upper(), match.group(2)
            method = {"API_ROUTE": "ANY", "ROUTE": "ANY", "REQUEST": "ANY", "HANDLEFUNC": "ANY", "HANDLE": "ANY"}.get(method, method)
            found.append(("api", f"{method} {route}", "HTTP route"))

    is_migration = "/migrations/" in f"/{lowered}" or "/alembic/versions/" in f"/{lowered}" or base.endswith(".sql")
    is_model = base in {"models.py", "model.py", "schema.prisma"} or "/models/" in f"/{lowered}"
    if is_migration or is_model:
        for regex, label in ((_CREATE_TABLE, "table"), (_DROP_TABLE, "table")):
            for match in regex.finditer(text):
                if regex is _DROP_TABLE:
                    found.append(("data", f"table {match.group(1)}", "table dropped by a migration"))
                else:
                    found.append(("data", f"table {match.group(1)}", "table"))
        for regex in (_ADD_COLUMN, _SQL_ADD_COLUMN, _ALTER_COLUMN):
            for match in regex.finditer(text):
                detail = "column altered" if regex is _ALTER_COLUMN else "column"
                found.append(("data", f"column {match.group(1)}.{match.group(2)}", detail))
        for regex in (_DROP_COLUMN, _SQL_DROP_COLUMN):
            for match in regex.finditer(text):
                found.append(("data", f"column {match.group(1)}.{match.group(2)}", "column dropped by a migration"))
        for match in _TABLENAME.finditer(text):
            found.append(("data", f"table {match.group(1)}", "ORM table"))
        if is_model:
            match = _MODEL_FIELD.match(text)
            if match:
                found.append(("data", f"field {match.group(1)}", "ORM model field"))
        if is_migration:
            for match in _SA_COLUMN.finditer(text):
                if not _ADD_COLUMN.search(text):
                    found.append(("data", f"column {match.group(1)}", "column in a migration"))

    for match in _ENV_READ.finditer(text):
        name = next(group for group in match.groups() if group)
        found.append(("config", name, "environment variable read by the code"))
    if base.startswith(".env"):
        match = _ENV_FILE_LINE.match(text)
        if match:
            found.append(("config", match.group(1), "environment variable in an example env file"))
    if base in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        match = _COMPOSE_ENV.match(text)
        if match:
            found.append(("config", match.group(1), "container environment variable"))
    if base in {"config.py", "settings.py"}:
        match = _SETTINGS_FIELD.match(text)
        if match and "(" not in stripped.split("=", 1)[0] and not stripped.startswith(("def ", "class ", "return", "import", "from")):
            found.append(("config", match.group(1), "application setting"))

    if base in _MANIFESTS:
        dep = None
        if base == "package.json":
            match = _PACKAGE_JSON_DEP.match(text)
            if match and match.group(1) not in _PACKAGE_META and not match.group(2).startswith(("vite", "tsc", "node ", "npm ")):
                dep = (match.group(1), match.group(2))
        elif base == "pyproject.toml":
            match = _PYPROJECT_DEP.match(text)
            if match:
                dep = (match.group(1), (match.group(2) or "").strip())
        elif base == "requirements.txt":
            match = _REQUIREMENT.match(text)
            if match:
                dep = (match.group(1), (match.group(2) or "").strip())
        elif base == "go.mod":
            match = _GO_REQUIRE.match(text)
            if match:
                dep = (match.group(1), match.group(2))
        if dep:
            found.append(("dependency", dep[0], dep[1] or "unpinned"))
    return found


def _merge_sides(path: str, items) -> list[SurfaceFinding]:
    """One finding per (level, name): added, removed, or changed when it is on both sides."""
    grouped: dict[tuple[str, str], dict] = {}
    for level, side, name, detail, line, text in items:
        key = (level, name)
        entry = grouped.setdefault(key, {"sides": {}, "detail": detail})
        entry["sides"].setdefault(side, (line, text, detail))
    out: list[SurfaceFinding] = []
    for (level, name), entry in grouped.items():
        sides = entry["sides"]
        if "added" in sides and "removed" in sides and " ".join(sides["added"][1].split()) == " ".join(sides["removed"][1].split()):
            continue  # The same line on both sides: moved or re-indented, not changed.
        if "added" in sides and "dropped" in sides["added"][2] and "removed" not in sides:
            line, text, detail = sides["added"]
            out.append(SurfaceFinding(level, "removed", name, detail, path, line, text.strip()))
            continue
        if "added" in sides and "removed" in sides:
            line, text, detail = sides["added"]
            old_detail = sides["removed"][2]
            if level == "dependency" and old_detail != detail:
                detail = f"{old_detail} → {detail}"
            out.append(SurfaceFinding(level, "changed", name, detail, path, line, text.strip()))
        elif "added" in sides:
            line, text, detail = sides["added"]
            out.append(SurfaceFinding(level, "added", name, detail, path, line, text.strip()))
        else:
            line, text, detail = sides["removed"]
            out.append(SurfaceFinding(level, "removed", name, detail, path, line, text.strip()))
    return out
