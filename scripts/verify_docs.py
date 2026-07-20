"""Verify the compact bilingual documentation and local links."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def main() -> int:
    errors: list[str] = []
    for language in ("cn", "en"):
        folder = ROOT / "docs" / language
        files = {path.name for path in folder.iterdir() if path.is_file()} if folder.is_dir() else set()
        if files != {"README.md"}:
            errors.append(f"docs/{language} must contain only README.md; found {sorted(files)}")
    documents = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "docs/cn/README.md", ROOT / "docs/en/README.md"]
    for document in documents:
        if not document.is_file():
            errors.append(f"missing document: {document.relative_to(ROOT)}")
            continue
        for target in LINK.findall(document.read_text(encoding="utf-8")):
            target = target.split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (document.parent / target).resolve()
            if ROOT not in resolved.parents and resolved != ROOT:
                errors.append(f"link escapes repository: {document.name} -> {target}")
            elif not resolved.exists():
                errors.append(f"broken link: {document.relative_to(ROOT)} -> {target}")
    if errors:
        raise SystemExit("documentation verification failed:\n- " + "\n- ".join(errors))
    print("compact bilingual documentation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
