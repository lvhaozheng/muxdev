from __future__ import annotations

import pytest

from muxdev.services.rule_sources import (
    RuleSourceError,
    _validate_public_url,
    html_to_markdown,
    normalize_uploaded_source,
)


def test_html_rule_source_extracts_article_and_removes_active_content() -> None:
    markdown, title = html_to_markdown(
        """
        <html><head><title>Design Guide</title></head><body>
          <nav>Ignore navigation</nav>
          <article><h1>Design</h1><p>Keep <strong>evidence</strong>.</p>
          <script>alert(1)</script></article>
        </body></html>
        """
    )

    assert title == "Design Guide"
    assert "# Design" in markdown
    assert "**evidence**" in markdown
    assert "Ignore navigation" not in markdown
    assert "alert" not in markdown


def test_uploaded_rule_source_rejects_binary_and_unknown_formats() -> None:
    with pytest.raises(RuleSourceError, match="Binary"):
        normalize_uploaded_source("rules.md", b"hello\x00world")
    with pytest.raises(RuleSourceError, match="Only Markdown"):
        normalize_uploaded_source("rules.pdf", b"not a pdf")


def test_public_url_validation_rejects_loopback(monkeypatch) -> None:
    monkeypatch.setattr(
        "muxdev.services.rule_sources.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))],
    )

    with pytest.raises(RuleSourceError, match="private or non-public"):
        _validate_public_url("http://example.test/rules")
