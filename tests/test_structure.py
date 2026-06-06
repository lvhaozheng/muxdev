from pathlib import Path


def test_src_contains_only_package_source():
    repo_root = Path(__file__).resolve().parents[1]
    src_files = [path.relative_to(repo_root) for path in (repo_root / "src").rglob("*") if path.is_file()]

    assert src_files
    assert all(parts[0] == "src" and parts[1] == "muxdev" for parts in (path.parts for path in src_files))


def test_canonical_layers_import():
    from muxdev.api import handle_jsonrpc, server_manifest, write_dashboard
    from muxdev.clients.sessions import HeadlessSubprocessBackend, SessionManager
    from muxdev.clients.stream import StreamAdapter
    from muxdev.config import get_account_info, get_install_plan
    from muxdev.models import ApprovalStatus, WorkflowDefinition
    from muxdev.services import LocalRagIndex, SkillRegistry, generate_final_report
    from muxdev.workflows import load_workflow

    assert callable(handle_jsonrpc)
    assert callable(server_manifest)
    assert callable(write_dashboard)
    assert HeadlessSubprocessBackend.__name__ == "HeadlessSubprocessBackend"
    assert SessionManager.__name__ == "SessionManager"
    assert StreamAdapter.__name__ == "StreamAdapter"
    assert get_account_info("mock").required is False
    assert get_install_plan("mock").provider == "mock"
    assert ApprovalStatus.PENDING == "pending"
    assert WorkflowDefinition.__name__ == "WorkflowDefinition"
    assert LocalRagIndex.__name__ == "LocalRagIndex"
    assert SkillRegistry.__name__ == "SkillRegistry"
    assert callable(generate_final_report)
    assert callable(load_workflow)


def test_legacy_shim_source_files_are_removed():
    package_root = Path(__file__).resolve().parents[1] / "src" / "muxdev"
    removed = [
        package_root / "mcp.py",
        package_root / "rag.py",
        package_root / "skills.py",
        package_root / "web.py",
        package_root / "providers" / "accounts.py",
        package_root / "providers" / "installers.py",
        package_root / "reports" / "__init__.py",
        package_root / "reports" / "final.py",
        package_root / "core" / "models.py",
        package_root / "orchestration" / "__init__.py",
        package_root / "safety" / "__init__.py",
        package_root / "safety" / "policy.py",
        package_root / "sessions" / "__init__.py",
        package_root / "sessions" / "backends.py",
        package_root / "sessions" / "manager.py",
        package_root / "stream" / "__init__.py",
        package_root / "stream" / "adapter.py",
        package_root / "workflow" / "__init__.py",
        package_root / "workflow" / "engine.py",
    ]

    assert all(not path.exists() for path in removed)
