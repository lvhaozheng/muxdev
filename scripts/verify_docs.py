"""Validate the mirrored bilingual documentation contract."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LANGUAGES = ("en", "cn")
FILES = {
    "README.md",
    "getting-started.md",
    "architecture.md",
    "concepts.md",
    "configuration.md",
    "operations.md",
    "security-and-trust.md",
    "development.md",
}
SECTION_PATTERN = re.compile(r"<!--\s*section:([a-z0-9-]+)\s*-->")
LINK_PATTERN = re.compile(r"\[[^]]+\]\(([^)]+)\)")
SOURCE_PATTERN = re.compile(r"`((?:src|tests|scripts)/[^`]+)`")


def main() -> int:
    errors: list[str] = []
    _check_layout(errors)
    _check_mirrors(errors)
    _check_links_and_sources(errors)
    _check_root_entries(errors)
    if errors:
        raise SystemExit("documentation contract failed:\n- " + "\n- ".join(errors))
    print("bilingual documentation contract verified")
    return 0


def _check_layout(errors: list[str]) -> None:
    entries = {path.name for path in DOCS.iterdir()}
    if entries != set(LANGUAGES):
        errors.append(f"docs must contain only en/ and cn/; found: {sorted(entries)}")
    for language in LANGUAGES:
        folder = DOCS / language
        actual = {path.name for path in folder.iterdir() if path.is_file()} if folder.is_dir() else set()
        directories = [path.name for path in folder.iterdir() if path.is_dir()] if folder.is_dir() else []
        if actual != FILES:
            errors.append(f"docs/{language} files differ: missing={sorted(FILES - actual)}, extra={sorted(actual - FILES)}")
        if directories:
            errors.append(f"docs/{language} must not contain nested directories: {directories}")


def _check_mirrors(errors: list[str]) -> None:
    for name in sorted(FILES):
        en = (DOCS / "en" / name).read_text(encoding="utf-8")
        cn = (DOCS / "cn" / name).read_text(encoding="utf-8")
        en_sections = SECTION_PATTERN.findall(en)
        cn_sections = SECTION_PATTERN.findall(cn)
        if not en_sections:
            errors.append(f"docs/en/{name} has no section markers")
        if en_sections != cn_sections:
            errors.append(f"section markers differ for {name}: en={en_sections}, cn={cn_sections}")
        if len(en_sections) != len(set(en_sections)):
            errors.append(f"duplicate section marker in {name}")
        expected_cn = f"../cn/{name}"
        expected_en = f"../en/{name}"
        if name == "README.md":
            expected_cn = "../cn/README.md"
            expected_en = "../en/README.md"
        if expected_cn not in en:
            errors.append(f"docs/en/{name} does not link to its Chinese counterpart")
        if expected_en not in cn:
            errors.append(f"docs/cn/{name} does not link to its English counterpart")


def _check_links_and_sources(errors: list[str]) -> None:
    markdown_files = [ROOT / "README.md", ROOT / "SECURITY.md"] + [DOCS / lang / name for lang in LANGUAGES for name in sorted(FILES)]
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, anchor = target.partition("#")
            target_path = path if not file_part else (path.parent / file_part).resolve()
            if not _inside_root(target_path) or not target_path.exists():
                errors.append(f"broken link in {path.relative_to(ROOT).as_posix()}: {raw_target}")
                continue
            if anchor and target_path.suffix.lower() == ".md":
                anchors = _heading_anchors(target_path.read_text(encoding="utf-8"))
                if anchor not in anchors:
                    errors.append(f"broken heading anchor in {path.relative_to(ROOT).as_posix()}: {raw_target}")
        for source in SOURCE_PATTERN.findall(text):
            clean = source.split("#", 1)[0].split(":", 1)[0]
            if not (ROOT / clean).exists():
                errors.append(f"missing source reference in {path.relative_to(ROOT).as_posix()}: {source}")


def _check_root_entries(errors: list[str]) -> None:
    for name in ("LICENSE", "CHANGELOG.md", "SECURITY.md", "README.md"):
        if not (ROOT / name).is_file():
            errors.append(f"missing root document: {name}")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for link in ("docs/en/README.md", "docs/cn/README.md"):
        if link not in readme:
            errors.append(f"root README is missing {link}")
    for link in ("docs/en/security-and-trust.md", "docs/cn/security-and-trust.md"):
        if link not in security:
            errors.append(f"SECURITY.md is missing {link}")


def _heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        heading = line.lstrip("#").strip()
        anchor = _slug(heading)
        count = counts.get(anchor, 0)
        counts[anchor] = count + 1
        anchors.add(anchor if count == 0 else f"{anchor}-{count}")
    return anchors


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"[^\w\-\u4e00-\u9fff ]", "", value)
    return re.sub(r"[ _]+", "-", value).strip("-")


def _inside_root(path: Path) -> bool:
    resolved = path.resolve()
    return resolved == ROOT or ROOT in resolved.parents


if __name__ == "__main__":
    raise SystemExit(main())
