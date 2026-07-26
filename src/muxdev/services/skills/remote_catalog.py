"""Remote GitHub Skill discovery and immutable managed downloads."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml

from .catalog import (
    MAX_SKILL_FILE_BYTES,
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILES,
    SkillCatalogError,
    audit_skill_source,
)


RemoteProvider = Literal["openai", "anthropic"]
_ALLOWED_REMOTE_HOSTS = {"api.github.com", "raw.githubusercontent.com"}
_CACHE_SECONDS = 600
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 50

_PROVIDERS: dict[RemoteProvider, dict[str, object]] = {
    "openai": {
        "label": "OpenAI",
        "owner": "openai",
        "repo": "skills",
        "ref": "main",
        "roots": ("skills/.curated", "skills/.experimental"),
    },
    "anthropic": {
        "label": "Anthropic",
        "owner": "anthropics",
        "repo": "skills",
        "ref": "main",
        "roots": ("skills",),
    },
}

_TREE_CACHE: dict[RemoteProvider, tuple[float, str, list[dict[str, Any]]]] = {}


class RemoteSkillError(SkillCatalogError):
    """A safe, user-facing remote catalog error."""


class RemoteSkillRateLimitError(RemoteSkillError):
    def __init__(self, message: str, *, retry_after_ms: int) -> None:
        super().__init__(message)
        self.retry_after_ms = max(1000, retry_after_ms)


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_REMOTE_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise RemoteSkillError("GitHub redirected to an unsupported host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class GitHubSkillLocation:
    owner: str
    repo: str
    ref: str
    path: str

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def source_url(self) -> str:
        suffix = f"/tree/{quote(self.ref, safe='')}"
        if self.path:
            suffix += "/" + "/".join(quote(part, safe="") for part in self.path.split("/"))
        return f"https://github.com/{self.owner}/{self.repo}{suffix}"


class GitHubRemoteSkillClient:
    """Small GitHub client with bounded responses and restricted redirects."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get(
            "MUXDEV_GITHUB_TOKEN"
        )
        self._opener = build_opener(_RestrictedRedirectHandler())

    def _headers(self, *, api: bool) -> dict[str, str]:
        headers = {
            "Accept": (
                "application/vnd.github+json"
                if api
                else "text/plain, application/octet-stream;q=0.9"
            ),
            "User-Agent": "muxdev-skill-catalog/1",
        }
        if api:
            headers["X-GitHub-Api-Version"] = "2022-11-28"
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _retry_after_ms(error: HTTPError) -> int:
        retry_after = str(error.headers.get("Retry-After") or "").strip()
        if retry_after.isdigit():
            return max(1, int(retry_after)) * 1000
        reset = str(error.headers.get("X-RateLimit-Reset") or "").strip()
        if reset.isdigit():
            return max(1, int(reset) - int(time.time())) * 1000
        return 60_000

    def _read(self, url: str, *, limit: int, api: bool) -> bytes:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_REMOTE_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise RemoteSkillError("Remote Skill requests must use approved GitHub hosts")
        try:
            with self._opener.open(
                Request(url, headers=self._headers(api=api)),
                timeout=10,
            ) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if api and "json" not in content_type:
                    raise RemoteSkillError("GitHub returned a non-JSON API response")
                payload = response.read(limit + 1)
        except HTTPError as exc:
            if exc.code in {403, 429}:
                raise RemoteSkillRateLimitError(
                    "GitHub request rate limit was reached",
                    retry_after_ms=self._retry_after_ms(exc),
                ) from exc
            if exc.code == 404:
                raise FileNotFoundError("GitHub repository, revision, or path was not found") from exc
            raise RemoteSkillError(f"GitHub request failed with HTTP {exc.code}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise RemoteSkillError("GitHub request failed or timed out") from exc
        if len(payload) > limit:
            raise RemoteSkillError("GitHub response exceeded the allowed size")
        return payload

    def _json(self, url: str, *, limit: int = 5 * 1024 * 1024) -> Mapping[str, Any]:
        try:
            value = json.loads(self._read(url, limit=limit, api=True))
        except json.JSONDecodeError as exc:
            raise RemoteSkillError("GitHub returned invalid JSON") from exc
        if not isinstance(value, Mapping):
            raise RemoteSkillError("GitHub returned an unexpected API response")
        return value

    def resolve_commit(self, owner: str, repo: str, ref: str) -> str:
        value = self._json(
            f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/commits/"
            f"{quote(ref, safe='')}",
            limit=1024 * 1024,
        )
        sha = str(value.get("sha") or "")
        if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha.lower()):
            raise RemoteSkillError("GitHub did not return a valid commit SHA")
        return sha.lower()

    def tree(self, owner: str, repo: str, commit: str) -> list[dict[str, Any]]:
        value = self._json(
            f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/git/trees/"
            f"{quote(commit)}?recursive=1",
            limit=10 * 1024 * 1024,
        )
        if value.get("truncated"):
            raise RemoteSkillError("GitHub repository tree is too large to inspect safely")
        tree = value.get("tree")
        if not isinstance(tree, list):
            raise RemoteSkillError("GitHub repository tree is unavailable")
        result: list[dict[str, Any]] = []
        for item in tree:
            if not isinstance(item, Mapping):
                continue
            path = str(item.get("path") or "")
            if not path or "\\" in path:
                continue
            result.append(
                {
                    "path": path,
                    "type": str(item.get("type") or ""),
                    "mode": str(item.get("mode") or ""),
                    "size": int(item.get("size") or 0),
                    "sha": str(item.get("sha") or ""),
                }
            )
        return result

    def read_file(
        self,
        owner: str,
        repo: str,
        commit: str,
        path: str,
        *,
        limit: int = MAX_SKILL_FILE_BYTES,
    ) -> bytes:
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        return self._read(
            f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/"
            f"{quote(commit)}/{encoded_path}",
            limit=limit,
            api=False,
        )


def _safe_segment(value: str, label: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise RemoteSkillError(f"Invalid GitHub {label}")
    return value


def _safe_repo_path(value: str) -> str:
    value = unquote(value).strip("/")
    if not value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RemoteSkillError("GitHub Skill path contains unsafe segments")
    return path.as_posix()


def parse_github_skill_url(url: str, *, explicit_ref: str | None = None) -> GitHubSkillLocation:
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise RemoteSkillError("Use a public HTTPS github.com repository or directory URL")
    parts = [unquote(item) for item in parsed.path.strip("/").split("/") if item]
    if len(parts) < 2:
        raise RemoteSkillError("GitHub URL must include an owner and repository")
    owner = _safe_segment(parts[0], "owner")
    repo = _safe_segment(parts[1].removesuffix(".git"), "repository")
    ref = explicit_ref or "main"
    path = ""
    if len(parts) > 2:
        marker = parts[2]
        if marker not in {"tree", "blob"} or len(parts) < 4:
            raise RemoteSkillError("GitHub URL must point to a repository, tree, or SKILL.md")
        ref = explicit_ref or _safe_segment(parts[3], "revision")
        path_parts = parts[4:]
        if marker == "blob":
            if not path_parts or path_parts[-1] != "SKILL.md":
                raise RemoteSkillError("GitHub blob URL must point to SKILL.md")
            path_parts = path_parts[:-1]
        path = _safe_repo_path("/".join(path_parts))
    return GitHubSkillLocation(
        owner=owner,
        repo=repo,
        ref=_safe_segment(ref, "revision"),
        path=path,
    )


def _frontmatter(payload: bytes, fallback_name: str) -> tuple[str, str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RemoteSkillError("Remote SKILL.md must be UTF-8 text") from exc
    metadata: Mapping[str, object] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                parsed = yaml.safe_load(parts[1])
            except yaml.YAMLError:
                parsed = {}
            if isinstance(parsed, Mapping):
                metadata = parsed
    name = str(metadata.get("name") or fallback_name).strip()[:120]
    description = str(metadata.get("description") or "").strip()[:2000]
    return name or fallback_name, description


def _license_label(payload: bytes | None) -> str | None:
    if not payload:
        return None
    text = payload.decode("utf-8", errors="ignore").lower()[:8192]
    if "apache license" in text and "version 2.0" in text:
        return "Apache-2.0"
    if "mit license" in text:
        return "MIT"
    if "creative commons zero" in text or "cc0 1.0" in text:
        return "CC0-1.0"
    if "bsd 3-clause" in text:
        return "BSD-3-Clause"
    return "来源包含许可证文件"


def _provider_tree(
    provider: RemoteProvider,
    client: GitHubRemoteSkillClient,
) -> tuple[str, list[dict[str, Any]]]:
    now = time.monotonic()
    cached = _TREE_CACHE.get(provider)
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1], cached[2]
    config = _PROVIDERS[provider]
    owner = str(config["owner"])
    repo = str(config["repo"])
    commit = client.resolve_commit(owner, repo, str(config["ref"]))
    tree = client.tree(owner, repo, commit)
    _TREE_CACHE[provider] = (now, commit, tree)
    return commit, tree


def _within_roots(path: str, roots: Iterable[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)


def _files_for_path(tree: Iterable[Mapping[str, Any]], path: str) -> list[dict[str, Any]]:
    prefix = path.rstrip("/") + "/" if path else ""
    result = [
        dict(item)
        for item in tree
        if str(item.get("type")) == "blob"
        and str(item.get("path") or "").startswith(prefix)
    ]
    return result


def _remote_item(
    provider: RemoteProvider,
    commit: str,
    tree: list[dict[str, Any]],
    skill_file: Mapping[str, Any],
    client: GitHubRemoteSkillClient,
) -> dict[str, Any]:
    config = _PROVIDERS[provider]
    owner = str(config["owner"])
    repo = str(config["repo"])
    skill_path = PurePosixPath(str(skill_file["path"])).parent.as_posix()
    fallback = PurePosixPath(skill_path).name
    payload = client.read_file(owner, repo, commit, str(skill_file["path"]))
    name, description = _frontmatter(payload, fallback)
    files = _files_for_path(tree, skill_path)
    license_item = next(
        (
            item
            for item in files
            if PurePosixPath(str(item["path"])).name.lower()
            in {"license", "license.md", "license.txt"}
        ),
        None,
    )
    license_payload = (
        client.read_file(owner, repo, commit, str(license_item["path"]))
        if license_item and int(license_item.get("size") or 0) <= MAX_SKILL_FILE_BYTES
        else None
    )
    location = GitHubSkillLocation(
        owner=owner,
        repo=repo,
        ref=commit,
        path=skill_path,
    )
    return {
        "catalog_id": hashlib.sha256(
            f"{provider}\0{owner}/{repo}\0{skill_path}\0{commit}".encode("utf-8")
        ).hexdigest()[:24],
        "provider": provider,
        "provider_label": str(config["label"]),
        "publisher": owner,
        "repository": f"{owner}/{repo}",
        "name": name,
        "description": description,
        "path": skill_path,
        "ref": str(config["ref"]),
        "commit_sha": commit,
        "source_url": location.source_url,
        "license": _license_label(license_payload),
        "file_count": len(files),
        "script_count": sum(
            1
            for item in files
            if "/scripts/" in f"/{str(item['path']).lower()}/"
        ),
        "trust": "publisher_verified",
    }


def search_remote_skills(
    query: str = "",
    *,
    provider: Literal["all", "openai", "anthropic"] = "all",
    cursor: str | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    client: GitHubRemoteSkillClient | None = None,
) -> dict[str, Any]:
    client = client or GitHubRemoteSkillClient()
    query = query.strip().casefold()
    page_size = min(max(1, page_size), _MAX_PAGE_SIZE)
    try:
        offset = max(0, int(cursor or "0"))
    except ValueError as exc:
        raise RemoteSkillError("Remote Skill cursor is invalid") from exc
    providers: list[RemoteProvider] = (
        ["openai", "anthropic"] if provider == "all" else [provider]
    )
    candidates: list[tuple[RemoteProvider, str, list[dict[str, Any]], dict[str, Any]]] = []
    for selected in providers:
        commit, tree = _provider_tree(selected, client)
        roots = tuple(str(item) for item in _PROVIDERS[selected]["roots"])
        for item in tree:
            path = str(item.get("path") or "")
            if (
                item.get("type") == "blob"
                and path.endswith("/SKILL.md")
                and _within_roots(path, roots)
            ):
                searchable = path.casefold().replace("-", " ").replace("_", " ")
                if not query or query in searchable:
                    candidates.append((selected, commit, tree, item))
    candidates.sort(key=lambda item: (item[0], str(item[3]["path"])))
    page = candidates[offset : offset + page_size]
    items = [
        _remote_item(selected, commit, tree, skill_file, client)
        for selected, commit, tree, skill_file in page
    ]
    next_offset = offset + len(page)
    return {
        "schema_version": "muxdev.remote-skills.v1",
        "query": query,
        "provider": provider,
        "items": items,
        "next_cursor": str(next_offset) if next_offset < len(candidates) else None,
        "total": len(candidates),
    }


def download_remote_skill(
    url: str,
    destination: Path,
    *,
    ref: str | None = None,
    client: GitHubRemoteSkillClient | None = None,
) -> dict[str, Any]:
    client = client or GitHubRemoteSkillClient()
    location = parse_github_skill_url(url, explicit_ref=ref)
    commit = client.resolve_commit(location.owner, location.repo, location.ref)
    tree = client.tree(location.owner, location.repo, commit)
    prefix = location.path.rstrip("/") + "/" if location.path else ""
    files = [
        item
        for item in tree
        if str(item.get("type")) == "blob"
        and str(item.get("path") or "").startswith(prefix)
    ]
    if not files or not any(str(item["path"]).endswith("/SKILL.md") or str(item["path"]) == "SKILL.md" for item in files):
        raise RemoteSkillError("Selected GitHub directory does not contain SKILL.md")
    if any(
        str(item.get("mode")) == "120000"
        or str(item.get("type")) not in {"blob"}
        for item in files
    ):
        raise RemoteSkillError("Remote Skill sources cannot contain symbolic links")
    if len(files) > MAX_SOURCE_FILES:
        raise RemoteSkillError("Skill source exceeds the 500 file limit")
    total = sum(int(item.get("size") or 0) for item in files)
    if total > MAX_SOURCE_BYTES:
        raise RemoteSkillError("Skill source exceeds the 20 MiB limit")
    if any(int(item.get("size") or 0) > MAX_SKILL_FILE_BYTES for item in files):
        raise RemoteSkillError("Remote Skill contains a file larger than 256 KiB")

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for item in files:
            remote_path = str(item["path"])
            relative = PurePosixPath(remote_path).relative_to(
                PurePosixPath(location.path) if location.path else PurePosixPath(".")
            )
            target = destination.joinpath(*relative.parts)
            resolved = target.resolve()
            try:
                resolved.relative_to(destination)
            except ValueError as exc:
                raise RemoteSkillError("Remote Skill path escapes the download directory") from exc
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(
                client.read_file(
                    location.owner,
                    location.repo,
                    commit,
                    remote_path,
                    limit=MAX_SKILL_FILE_BYTES,
                )
            )
        audit = audit_skill_source(destination)
    except BaseException:
        import shutil

        shutil.rmtree(destination, ignore_errors=True)
        raise
    license_file = next(
        (
            item
            for item in audit["files"]
            if PurePosixPath(str(item["path"])).name.lower()
            in {"license", "license.md", "license.txt"}
        ),
        None,
    )
    license_payload = (
        (destination / str(license_file["path"])).read_bytes()
        if license_file
        else None
    )
    return {
        "location": {
            "owner": location.owner,
            "repo": location.repo,
            "repository": location.repository,
            "path": location.path,
            "requested_ref": location.ref,
            "commit_sha": commit,
            "source_url": GitHubSkillLocation(
                owner=location.owner,
                repo=location.repo,
                ref=commit,
                path=location.path,
            ).source_url,
        },
        "license": _license_label(license_payload),
        "audit": audit,
    }


__all__ = [
    "GitHubRemoteSkillClient",
    "GitHubSkillLocation",
    "RemoteSkillError",
    "RemoteSkillRateLimitError",
    "download_remote_skill",
    "parse_github_skill_url",
    "search_remote_skills",
]
