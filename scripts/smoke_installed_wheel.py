from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".test_workspaces/wheel-runtime").resolve()
    root.mkdir(parents=True, exist_ok=True)

    from fastapi.testclient import TestClient

    from muxdev import __version__
    from muxdev.api import create_app, server_manifest
    from muxdev.runtime import RunEngine
    from muxdev.services.dsse import export_dsse, verify_dsse
    from muxdev.services.evidence_verify import verify_evidence_report

    application = create_app(root)
    with TestClient(application) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["tables"] == 12

    engine = RunEngine(root)
    try:
        run = engine.run("installed wheel smoke check", provider="mock", profile="lite")
        evidence = verify_evidence_report(run.report_path, store=engine.store)
        envelope = export_dsse(run.report_path)
        attestation = verify_dsse(Path(str(envelope["path"])), report_path=run.report_path)
    finally:
        engine.store.close()
    if not evidence["valid"] or not attestation["valid"]:
        raise SystemExit("installed wheel Evidence/DSSE verification failed")
    print(json.dumps({
        "version": __version__,
        "tables": health.json()["tables"],
        "run_status": str(run.status),
        "gate_status": evidence["gate_status"],
        "evidence_valid": evidence["valid"],
        "attestation_digest_valid": attestation["valid"],
        "mcp_tools": len(server_manifest(root)["tools"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
