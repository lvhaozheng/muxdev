"""TTS batch engine for GPT-SoVITS style local synthesis.

The engine is deliberately backend-light: GPT-SoVITS installations differ
between API versions and local inference scripts, so callers can provide either
a local API URL or a subprocess command template. The stable contract is the
project-facing side: read ``chunks/*.txt``, write ``audio/*.wav``, and emit a
timeline with measured WAV durations.
"""

from __future__ import annotations

import json
import io
import os
import shlex
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Literal

import httpx

from ..core.platforms import hidden_subprocess_kwargs


BackendKind = Literal["api", "subprocess"]


@dataclass(frozen=True)
class TtsChunk:
    index: int
    path: Path
    stem: str
    text: str
    output_path: Path


@dataclass(frozen=True)
class TtsSegmentResult:
    index: int
    stem: str
    text_path: str
    audio_path: str
    start: float
    end: float
    duration: float
    attempts: int
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "index": self.index,
            "stem": self.stem,
            "text_path": self.text_path,
            "audio_path": self.audio_path,
            "start": round(self.start, 6),
            "end": round(self.end, 6),
            "duration": round(self.duration, 6),
            "attempts": self.attempts,
            "status": self.status,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class TtsEngineResult:
    chunks: int
    generated: int
    skipped: int
    failed: int
    audio_dir: str
    timeline_path: str
    backend: BackendKind
    segments: list[TtsSegmentResult]

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "chunks": self.chunks,
            "generated": self.generated,
            "skipped": self.skipped,
            "failed": self.failed,
            "audio_dir": self.audio_dir,
            "timeline_path": self.timeline_path,
            "backend": self.backend,
            "segments": [segment.to_dict() for segment in self.segments],
        }


class TtsEngineError(RuntimeError):
    """Raised when the TTS engine cannot be configured or run."""


def synthesize_chunks(
    *,
    chunks_dir: Path,
    audio_dir: Path,
    timeline_path: Path,
    resume: bool = False,
    retries: int = 1,
    api_url: str | None = None,
    api_method: str = "GET",
    api_params: dict[str, str] | None = None,
    command: str | None = None,
    script: Path | None = None,
    python: str = "python",
    timeout: float = 120.0,
) -> TtsEngineResult:
    """Synthesize text chunks to WAV files and write a timeline.

    Args:
        chunks_dir: Directory containing ``*.txt`` chunk files.
        audio_dir: Output directory for ``*.wav`` files.
        timeline_path: JSON timeline output path.
        resume: Skip existing valid WAV files instead of regenerating them.
        retries: Number of retries after the first failed attempt per segment.
        api_url: GPT-SoVITS local API URL. Mutually exclusive with command/script.
        api_method: ``GET`` or ``POST`` for local API calls.
        api_params: Extra query or JSON parameters sent to the API.
        command: Subprocess command template with placeholders such as
            ``{input}``, ``{output}``, ``{text}``, ``{stem}``, and ``{index}``.
        script: Convenience subprocess mode: run ``python <script> --text-file
            {input} --output {output}``.
        python: Python executable used with ``script``.
        timeout: Per-attempt timeout in seconds.
    """

    chunks_dir = chunks_dir.resolve()
    audio_dir = audio_dir.resolve()
    timeline_path = timeline_path.resolve()
    if not chunks_dir.exists():
        raise TtsEngineError(f"chunks directory not found: {chunks_dir}")
    chunks = _load_chunks(chunks_dir, audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    retries = max(0, retries)
    backend = _resolve_backend(api_url=api_url, command=command, script=script)

    segments: list[TtsSegmentResult] = []
    generated = 0
    skipped = 0
    cursor = 0.0

    for chunk in chunks:
        if resume and chunk.output_path.exists():
            duration = wav_duration(chunk.output_path)
            if duration > 0:
                segments.append(
                    _segment_result(
                        chunk,
                        start=cursor,
                        duration=duration,
                        attempts=0,
                        status="skipped",
                    )
                )
                cursor += duration
                skipped += 1
                continue

        attempts = 0
        error: Exception | None = None
        temp_output = chunk.output_path.with_name(f"{chunk.output_path.stem}.tmp.wav")
        _unlink_best_effort(temp_output)
        for attempt in range(retries + 1):
            attempts = attempt + 1
            try:
                _synthesize_one(
                    chunk=chunk,
                    temp_output=temp_output,
                    backend=backend,
                    api_url=api_url,
                    api_method=api_method,
                    api_params=api_params or {},
                    command=command,
                    script=script,
                    python=python,
                    timeout=timeout,
                )
                duration = wav_duration(temp_output)
                if duration <= 0:
                    raise TtsEngineError(f"generated wav has zero duration: {temp_output}")
                _promote_wav(temp_output, chunk.output_path)
                segments.append(
                    _segment_result(
                        chunk,
                        start=cursor,
                        duration=duration,
                        attempts=attempts,
                        status="generated",
                    )
                )
                cursor += duration
                generated += 1
                error = None
                break
            except Exception as exc:  # noqa: BLE001 - preserve backend errors in result.
                error = exc
                _unlink_best_effort(temp_output)
        if error is not None:
            segments.append(
                _segment_result(
                    chunk,
                    start=cursor,
                    duration=0.0,
                    attempts=attempts,
                    status="failed",
                    error=str(error),
                )
            )

    failed = sum(1 for segment in segments if segment.status == "failed")
    payload = {
        "version": 1,
        "backend": backend,
        "chunks_dir": str(chunks_dir),
        "audio_dir": str(audio_dir),
        "total_duration": round(cursor, 6),
        "segments": [segment.to_dict() for segment in segments],
    }
    timeline_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return TtsEngineResult(
        chunks=len(chunks),
        generated=generated,
        skipped=skipped,
        failed=failed,
        audio_dir=str(audio_dir),
        timeline_path=str(timeline_path),
        backend=backend,
        segments=segments,
    )


def wav_duration(path: Path) -> float:
    """Return WAV duration in seconds using the standard library."""
    try:
        with wave.open(io.BytesIO(path.read_bytes()), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return frames / float(rate) if rate else 0.0
    except (OSError, wave.Error):
        return 0.0


def _promote_wav(temp_output: Path, output_path: Path) -> None:
    output_path.write_bytes(temp_output.read_bytes())
    _unlink_best_effort(temp_output)


def _unlink_best_effort(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _resolve_backend(*, api_url: str | None, command: str | None, script: Path | None) -> BackendKind:
    configured = [bool(api_url), bool(command), bool(script)]
    if sum(configured) != 1:
        raise TtsEngineError("configure exactly one GPT-SoVITS backend: --api-url, --command, or --script")
    return "api" if api_url else "subprocess"


def _load_chunks(chunks_dir: Path, audio_dir: Path) -> list[TtsChunk]:
    chunks: list[TtsChunk] = []
    for index, path in enumerate(sorted(chunks_dir.glob("*.txt")), start=1):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        chunks.append(TtsChunk(index=index, path=path, stem=path.stem, text=text, output_path=audio_dir / f"{path.stem}.wav"))
    if not chunks:
        raise TtsEngineError(f"no non-empty txt chunks found in: {chunks_dir}")
    return chunks


def _synthesize_one(
    *,
    chunk: TtsChunk,
    temp_output: Path,
    backend: BackendKind,
    api_url: str | None,
    api_method: str,
    api_params: dict[str, str],
    command: str | None,
    script: Path | None,
    python: str,
    timeout: float,
) -> None:
    if backend == "api":
        assert api_url is not None
        _call_api(
            api_url=api_url,
            method=api_method,
            params=api_params,
            chunk=chunk,
            output=temp_output,
            timeout=timeout,
        )
        return
    _call_subprocess(
        chunk=chunk,
        output=temp_output,
        command=command,
        script=script,
        python=python,
        timeout=timeout,
    )


def _call_api(
    *,
    api_url: str,
    method: str,
    params: dict[str, str],
    chunk: TtsChunk,
    output: Path,
    timeout: float,
) -> None:
    payload = {"text": chunk.text, **params}
    method = method.upper()
    with httpx.Client(timeout=timeout) as client:
        if method == "GET":
            response = client.get(api_url, params=payload)
        elif method == "POST":
            response = client.post(api_url, json=payload)
        else:
            raise TtsEngineError(f"unsupported API method: {method}")
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "json" in content_type.lower():
        data = response.json()
        audio = data.get("audio") or data.get("wav")
        if isinstance(audio, str):
            output.write_bytes(Path(audio).read_bytes())
            return
        raise TtsEngineError("API returned JSON without an audio/wav file path")
    output.write_bytes(response.content)


def _call_subprocess(
    *,
    chunk: TtsChunk,
    output: Path,
    command: str | None,
    script: Path | None,
    python: str,
    timeout: float,
) -> None:
    if command:
        argv = _render_command(command, chunk=chunk, output=output)
    else:
        assert script is not None
        argv = [
            python,
            str(script),
            "--text-file",
            str(chunk.path),
            "--output",
            str(output),
        ]
    completed = subprocess.run(
        argv,
        cwd=chunk.path.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise TtsEngineError(f"subprocess failed for {chunk.path.name}: {detail or completed.returncode}")
    if not output.exists():
        raise TtsEngineError(f"subprocess did not create expected wav: {output}")


def _render_command(template: str, *, chunk: TtsChunk, output: Path) -> list[str]:
    values = {
        "input": str(chunk.path),
        "text_path": str(chunk.path),
        "output": str(output),
        "text": chunk.text,
        "stem": chunk.stem,
        "index": str(chunk.index),
    }
    known_fields = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    unknown = known_fields - values.keys()
    if unknown:
        raise TtsEngineError(f"unknown command placeholders: {', '.join(sorted(unknown))}")
    return shlex.split(template.format(**values), posix=os.name != "nt")


def _segment_result(
    chunk: TtsChunk,
    *,
    start: float,
    duration: float,
    attempts: int,
    status: str,
    error: str | None = None,
) -> TtsSegmentResult:
    return TtsSegmentResult(
        index=chunk.index,
        stem=chunk.stem,
        text_path=str(chunk.path),
        audio_path=str(chunk.output_path),
        start=start,
        end=start + duration,
        duration=duration,
        attempts=attempts,
        status=status,
        error=error,
    )
