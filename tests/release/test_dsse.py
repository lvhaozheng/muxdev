from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from muxdev.runtime import RunEngine
from muxdev.services.dsse import export_dsse, verify_dsse


def test_signed_dsse_binds_the_report_without_copying_records(workspace: Path) -> None:
    engine = RunEngine(workspace)
    result = engine.run("attest marker", provider="mock", profile="lite")
    private = Ed25519PrivateKey.generate()
    private_path, public_path = workspace / "private.pem", workspace / "public.pem"
    private_path.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    public_path.write_bytes(private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    exported = export_dsse(result.report_path, private_key=private_path)
    verified = verify_dsse(Path(str(exported["path"])), public_key=public_path, report_path=result.report_path)
    assert verified["valid"] and verified["signed"]
    assert "records" not in verified["statement"]["predicate"]
    wrong = Ed25519PrivateKey.generate().public_key()
    wrong_path = workspace / "wrong-public.pem"
    wrong_path.write_bytes(wrong.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    assert not verify_dsse(
        Path(str(exported["path"])), public_key=wrong_path, report_path=result.report_path
    )["valid"]
    engine.store.close()
