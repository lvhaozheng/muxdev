from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import uuid
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from typer.testing import CliRunner

from muxdev.cli import app
from muxdev.services.tts_engine import synthesize_chunks, wav_duration


runner = CliRunner()


def test_tts_engine_subprocess_retries_only_failed_segment_and_writes_timeline() -> None:
    temp_dir = _workspace_temp()
    try:
        chunks = temp_dir / "chunks"
        audio = temp_dir / "audio"
        chunks.mkdir()
        (chunks / "001.txt").write_text("第一段文本", encoding="utf-8")
        (chunks / "002.txt").write_text("第二段文本", encoding="utf-8")
        script = _write_fake_tts_script(temp_dir)

        result = synthesize_chunks(
            chunks_dir=chunks,
            audio_dir=audio,
            timeline_path=temp_dir / "timeline.json",
            command=f"{sys.executable} {script} --text-file {{input}} --output {{output}} --fail-once-stem 002",
            retries=1,
        )
        timeline = json.loads((temp_dir / "timeline.json").read_text(encoding="utf-8"))

        assert result.ok is True
        assert result.generated == 2
        assert result.failed == 0
        assert [segment.status for segment in result.segments] == ["generated", "generated"]
        assert [segment.attempts for segment in result.segments] == [1, 2]
        assert (audio / "001.wav").exists()
        assert (audio / "002.wav").exists()
        assert timeline["segments"][1]["attempts"] == 2
        assert timeline["segments"][0]["duration"] > 0
        assert timeline["segments"][1]["start"] >= timeline["segments"][0]["end"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_tts_engine_resume_skips_existing_valid_audio() -> None:
    temp_dir = _workspace_temp()
    try:
        chunks = temp_dir / "chunks"
        audio = temp_dir / "audio"
        chunks.mkdir()
        audio.mkdir()
        (chunks / "001.txt").write_text("已有音频", encoding="utf-8")
        (chunks / "002.txt").write_text("需要生成", encoding="utf-8")
        _write_wav(audio / "001.wav", duration=0.25)
        script = _write_fake_tts_script(temp_dir)

        result = synthesize_chunks(
            chunks_dir=chunks,
            audio_dir=audio,
            timeline_path=temp_dir / "timeline.json",
            command=f"{sys.executable} {script} --text-file {{input}} --output {{output}}",
            resume=True,
        )
        timeline = json.loads((temp_dir / "timeline.json").read_text(encoding="utf-8"))

        assert result.ok is True
        assert result.skipped == 1
        assert result.generated == 1
        assert [segment.status for segment in result.segments] == ["skipped", "generated"]
        assert wav_duration(audio / "001.wav") == 0.25
        assert timeline["segments"][0]["attempts"] == 0
        assert timeline["segments"][0]["status"] == "skipped"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_tts_engine_cli_runs_subprocess_backend() -> None:
    temp_dir = _workspace_temp()
    try:
        with _chdir(temp_dir):
            chunks = Path("chunks")
            chunks.mkdir()
            (chunks / "001.txt").write_text("CLI 合成测试", encoding="utf-8")
            script = _write_fake_tts_script(temp_dir)
            result = runner.invoke(
                app,
                [
                    "tts-engine",
                    "run",
                    "--command",
                    f"{sys.executable} {script} --text-file {{input}} --output {{output}}",
                    "--json",
                ],
            )
            payload = json.loads(result.stdout)

            assert result.exit_code == 0
            assert payload["ok"] is True
            assert payload["generated"] == 1
            assert Path(payload["timeline_path"]).name == "timeline.json"
            assert Path(payload["audio_dir"], "001.wav").exists()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_tts_engine_api_backend_writes_audio_and_timeline() -> None:
    temp_dir = _workspace_temp()
    server = _FakeTtsApi(("127.0.0.1", 0), _FakeTtsApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        chunks = temp_dir / "chunks"
        audio = temp_dir / "audio"
        chunks.mkdir()
        (chunks / "001.txt").write_text("API 合成测试", encoding="utf-8")

        result = synthesize_chunks(
            chunks_dir=chunks,
            audio_dir=audio,
            timeline_path=temp_dir / "timeline.json",
            api_url=f"http://127.0.0.1:{server.server_address[1]}/tts",
            api_method="POST",
            api_params={"text_language": "zh"},
        )
        timeline = json.loads((temp_dir / "timeline.json").read_text(encoding="utf-8"))

        assert result.ok is True
        assert result.backend == "api"
        assert result.generated == 1
        assert wav_duration(audio / "001.wav") > 0
        assert timeline["backend"] == "api"
        assert server.requests == 1
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(temp_dir, ignore_errors=True)


def _write_fake_tts_script(root: Path) -> Path:
    script = root / "fake_tts.py"
    script.write_text(
        """
from __future__ import annotations

import argparse
from pathlib import Path
import wave


def write_wav(path: Path, duration: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 8000
    frames = int(rate * duration)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\\0\\0" * frames)


parser = argparse.ArgumentParser()
parser.add_argument("--text-file", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--fail-once-stem", default="")
args = parser.parse_args()

text_path = Path(args.text_file)
output = Path(args.output)
marker = output.with_suffix(".attempt")
if text_path.stem == args.fail_once_stem and not marker.exists():
    marker.write_text("failed once", encoding="utf-8")
    raise SystemExit(3)
write_wav(output)
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _write_wav(path: Path, *, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_wav_bytes(duration=duration))


def _wav_bytes(*, duration: float = 0.2) -> bytes:
    rate = 8000
    frames = int(rate * duration)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


class _FakeTtsApi(ThreadingHTTPServer):
    requests: int = 0


class _FakeTtsApiHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
        self.server.requests += 1  # type: ignore[attr-defined]
        content_length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(content_length)
        if b"API" not in body:
            self.send_response(400)
            self.end_headers()
            return
        payload = _wav_bytes()
        self.send_response(200)
        self.send_header("content-type", "audio/wav")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


def _workspace_temp() -> Path:
    path = Path(".test_workspaces") / f"tts_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@contextmanager
def _chdir(path: Path) -> Iterator[None]:
    cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)
