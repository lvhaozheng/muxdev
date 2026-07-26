"""Safe local normalization for reusable Rule source documents."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_SOURCE_BYTES = 5 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
}
ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm"}


class RuleSourceError(ValueError):
    """A source could not be accepted without weakening the import boundary."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class _HTMLToMarkdown(HTMLParser):
    block_tags = {
        "article",
        "aside",
        "blockquote",
        "body",
        "div",
        "footer",
        "header",
        "main",
        "p",
        "section",
        "table",
        "tr",
    }
    skipped_tags = {"script", "style", "nav", "form", "noscript", "svg", "canvas"}

    def __init__(self, target_tag: str) -> None:
        super().__init__(convert_charrefs=True)
        self.target_tag = target_tag
        self.target_depth = 0
        self.skip_depth = 0
        self.output: list[str] = []
        self.links: list[str | None] = []
        self.title: list[str] = []
        self.in_title = False

    @property
    def active(self) -> bool:
        return self.target_tag == "document" or self.target_depth > 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = True
        if tag == self.target_tag:
            self.target_depth += 1
        elif self.target_depth and tag == self.target_tag:
            self.target_depth += 1
        if not self.active:
            return
        if tag in self.skipped_tags:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.block_tags:
            self.output.append("\n\n")
        elif tag == "br":
            self.output.append("\n")
        elif tag in {"ul", "ol"}:
            self.output.append("\n")
        elif tag == "li":
            self.output.append("\n- ")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.output.append(f"\n\n{'#' * int(tag[1])} ")
        elif tag == "pre":
            self.output.append("\n\n```\n")
        elif tag == "code":
            self.output.append("`")
        elif tag in {"strong", "b"}:
            self.output.append("**")
        elif tag in {"em", "i"}:
            self.output.append("*")
        elif tag == "a":
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if self.active and tag in self.skipped_tags and self.skip_depth:
            self.skip_depth -= 1
        elif self.active and not self.skip_depth:
            if tag in self.block_tags or tag in {"ul", "ol", "li"}:
                self.output.append("\n")
            elif tag == "pre":
                self.output.append("\n```\n")
            elif tag == "code":
                self.output.append("`")
            elif tag in {"strong", "b"}:
                self.output.append("**")
            elif tag in {"em", "i"}:
                self.output.append("*")
            elif tag == "a":
                href = self.links.pop() if self.links else None
                if href and not href.lower().startswith(("javascript:", "data:")):
                    self.output.append(f" ({href})")
        if tag == self.target_tag and self.target_depth:
            self.target_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        if self.active and not self.skip_depth:
            self.output.append(data)


def normalize_markdown(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in value.splitlines()]
    compact: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            compact.append(line.strip() if not line.startswith(("    ", "\t")) else line)
            blank = False
        elif not blank and compact:
            compact.append("")
            blank = True
    result = "\n".join(compact).strip()
    if not result:
        raise RuleSourceError("Rule source does not contain readable text")
    return result + "\n"


def html_to_markdown(value: str) -> tuple[str, str]:
    lowered = value.lower()
    target = (
        "article"
        if re.search(r"<article(?:\s|>)", lowered)
        else "main"
        if re.search(r"<main(?:\s|>)", lowered)
        else "body"
        if re.search(r"<body(?:\s|>)", lowered)
        else "document"
    )
    parser = _HTMLToMarkdown(target)
    parser.feed(value)
    parser.close()
    title = re.sub(r"\s+", " ", "".join(parser.title)).strip()
    return normalize_markdown("".join(parser.output)), title


def normalize_uploaded_source(
    filename: str,
    payload: bytes,
    *,
    content_type: str = "",
) -> tuple[str, dict[str, object]]:
    if len(payload) > MAX_SOURCE_BYTES:
        raise RuleSourceError("Rule source exceeds the 5 MiB limit")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise RuleSourceError("Only Markdown, text, and HTML Rule sources are supported")
    if b"\x00" in payload[:8192]:
        raise RuleSourceError("Binary Rule sources are not supported")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RuleSourceError("Rule source must be UTF-8 text") from exc
    if suffix in {".html", ".htm"} or content_type.split(";", 1)[0].strip().lower() in {
        "text/html",
        "application/xhtml+xml",
    }:
        markdown, title = html_to_markdown(text)
    else:
        markdown, title = normalize_markdown(text), ""
    return markdown, {
        "display_name": title or Path(filename).stem or filename,
        "content_type": content_type or ("text/html" if suffix in {".html", ".htm"} else "text/markdown"),
    }


def _validate_public_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise RuleSourceError("Rule source URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise RuleSourceError("Rule source URL must not contain credentials")
    if not parsed.hostname:
        raise RuleSourceError("Rule source URL is missing a host")
    try:
        port = parsed.port
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        }
    except (socket.gaierror, ValueError) as exc:
        raise RuleSourceError("Rule source host could not be resolved") from exc
    if not addresses:
        raise RuleSourceError("Rule source host did not resolve to an address")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise RuleSourceError("Rule source URL resolves to a private or non-public address")
    return value


def fetch_public_webpage(url: str) -> tuple[str, dict[str, object]]:
    opener = build_opener(_NoRedirect())
    current = url
    deadline = time.monotonic() + 10
    for redirect_count in range(6):
        current = _validate_public_url(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuleSourceError("Rule source URL exceeded the 10 second timeout")
        request = Request(
            current,
            headers={
                "User-Agent": "MuxDev-Rule-Importer/1.0",
                "Accept": "text/html,text/markdown,text/plain;q=0.9",
                "Accept-Encoding": "identity",
            },
        )
        try:
            response = opener.open(request, timeout=remaining)
        except HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location", "")
                if not location or redirect_count >= 5:
                    raise RuleSourceError("Rule source URL exceeded five redirects") from exc
                current = urljoin(current, location)
                continue
            raise RuleSourceError(f"Rule source URL returned HTTP {exc.code}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise RuleSourceError(f"Rule source URL could not be fetched: {exc}") from exc
        with response:
            content_type = response.headers.get_content_type().lower()
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise RuleSourceError(f"Unsupported Rule source content type: {content_type}")
            declared_length = response.headers.get("Content-Length")
            if declared_length:
                try:
                    if int(declared_length) > MAX_SOURCE_BYTES:
                        raise RuleSourceError("Rule source exceeds the 5 MiB limit")
                except ValueError:
                    pass
            payload = response.read(MAX_SOURCE_BYTES + 1)
            if len(payload) > MAX_SOURCE_BYTES:
                raise RuleSourceError("Rule source exceeds the 5 MiB limit")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset)
            except (LookupError, UnicodeDecodeError) as exc:
                raise RuleSourceError("Rule source page could not be decoded as text") from exc
            if content_type in {"text/html", "application/xhtml+xml"}:
                markdown, title = html_to_markdown(text)
            else:
                markdown, title = normalize_markdown(text), ""
            return markdown, {
                "display_name": title or urlsplit(current).hostname or "Imported webpage",
                "content_type": content_type,
                "original_url": current,
            }
    raise RuleSourceError("Rule source URL exceeded five redirects")


def source_digest(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def store_source_markdown(root: Path, markdown: str) -> tuple[str, Path]:
    digest = source_digest(markdown)
    target = root.resolve() / "rule-sources" / f"{digest}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(markdown, encoding="utf-8", newline="\n")
        temporary.replace(target)
    return digest, target


__all__ = [
    "MAX_SOURCE_BYTES",
    "RuleSourceError",
    "fetch_public_webpage",
    "html_to_markdown",
    "normalize_markdown",
    "normalize_uploaded_source",
    "source_digest",
    "store_source_markdown",
]
