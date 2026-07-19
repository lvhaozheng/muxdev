from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATE_EPOCH = "1735689600"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify a local muxdev release candidate without publishing it.")
    parser.add_argument("--output", type=Path, default=ROOT / "release-artifacts")
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    reproducible = None
    if not args.metadata_only:
        first = output / "build-a"
        second = output / "build-b"
        for folder in (first, second):
            if folder.exists():
                if folder.parent != output:
                    raise SystemExit("refusing to clean release directory outside the output root")
                shutil.rmtree(folder)
            folder.mkdir(parents=True, exist_ok=True)
            _run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(folder)])
            for sdist in folder.glob("*.tar.gz"):
                _normalize_sdist(sdist)
        first_hashes = _artifact_hashes(first)
        second_hashes = _artifact_hashes(second)
        reproducible = first_hashes == second_hashes
        if not reproducible:
            raise SystemExit("release builds are not byte reproducible under SOURCE_DATE_EPOCH")
        for source in sorted(first.iterdir()):
            if source.is_file():
                destination = output / source.name
                shutil.copy2(source, destination)
                artifacts.append(destination)
        _run([sys.executable, "-m", "twine", "check", *[str(path) for path in artifacts]])

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": "muxdev", "version": _version()}},
        "components": [
            {"type": "library", "name": requirement.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].strip(), "version": _installed_version(requirement)}
            for requirement in project.get("dependencies", [])
        ],
    }
    (output / "sbom.cdx.json").write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    licenses = [
        {"name": component["name"], "version": component["version"], "license": _installed_license(component["name"])}
        for component in sbom["components"]
    ]
    (output / "dependency-licenses.json").write_text(json.dumps(licenses, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    release_files = sorted([*artifacts, output / "sbom.cdx.json", output / "dependency-licenses.json"])
    checksums = {path.name: _sha256(path) for path in release_files}
    (output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())), encoding="utf-8")
    manifest = {
        "contract_version": "muxdev.release-manifest.v1",
        "version": _version(),
        "git_commit": _git_commit(),
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "reproducible_build": reproducible,
        "supported_python": ["3.11", "3.12", "3.13"],
        "supported_os": ["windows", "linux", "macos"],
        "benchmark": {"status": "pending_live_gate", "claims_allowed": False},
        "artifacts": checksums,
        "published": False,
    }
    (output / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ready" if args.metadata_only or reproducible else "failed", "output": str(output), "manifest": manifest}, sort_keys=True))
    return 0


def _run(command: list[str]) -> None:
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
    env["PYTHONHASHSEED"] = "0"
    local_temp = ROOT / ".test_workspaces" / "release-build-tmp"
    local_temp.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(local_temp)
    env["TMP"] = str(local_temp)
    completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise SystemExit(f"release command failed: {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")


def _artifact_hashes(folder: Path) -> dict[str, str]:
    return {path.name: _sha256(path) for path in folder.iterdir() if path.is_file()}


def _normalize_sdist(path: Path) -> None:
    """Canonicalize tar/gzip metadata that setuptools leaves wall-clock based."""
    epoch = int(SOURCE_DATE_EPOCH)
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, "r:gz") as source:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            members.append((member, data))
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w", format=tarfile.GNU_FORMAT) as target:
        for member, data in sorted(members, key=lambda item: item[0].name):
            normalized = tarfile.TarInfo(member.name)
            normalized.type = member.type
            normalized.linkname = member.linkname
            normalized.size = len(data) if data is not None else 0
            normalized.mtime = epoch
            normalized.uid = normalized.gid = 0
            normalized.uname = normalized.gname = ""
            normalized.mode = 0o755 if member.isdir() else 0o644
            target.addfile(normalized, io.BytesIO(data) if data is not None else None)
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, compresslevel=9, mtime=epoch) as archive:
        archive.write(tar_stream.getvalue())
    path.write_bytes(compressed.getvalue())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version() -> str:
    namespace: dict[str, object] = {}
    exec((ROOT / "src" / "muxdev" / "_version.py").read_text(encoding="utf-8"), namespace)
    return str(namespace["__version__"])


def _installed_version(requirement: str) -> str:
    name = requirement.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].strip()
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _installed_license(name: str) -> str:
    try:
        metadata = importlib.metadata.metadata(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
    return str(metadata.get("License-Expression") or metadata.get("License") or "unknown")


def _git_commit() -> str:
    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
