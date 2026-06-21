"""Offline document scroller video rendering.

The renderer turns a PDF or a set of PNG pages into a smooth scrolling frame
sequence, then delegates only the final MP4 encode to FFmpeg. It intentionally
does not use a browser, OBS, or screen capture APIs.
"""

from __future__ import annotations

import glob
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..core.platforms import hidden_subprocess_kwargs, split_command_line

try:  # Pillow is an install-time dependency, but keep import errors actionable.
    from PIL import Image, ImageColor, ImageOps
except ModuleNotFoundError:  # pragma: no cover - exercised only on broken installs.
    Image = None  # type: ignore[assignment]
    ImageColor = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


InputKind = Literal["pdf", "png"]


@dataclass(frozen=True)
class TimelineSegment:
    index: int
    start: float
    end: float


@dataclass(frozen=True)
class RenderTimeline:
    duration: float
    segments: list[TimelineSegment]


@dataclass(frozen=True)
class OfflineRenderResult:
    mode: str
    input_path: str
    input_kind: InputKind
    pages: int
    frames: int
    duration: float
    width: int
    height: int
    fps: int
    output_path: str
    work_dir: str
    source_strip_path: str
    ffmpeg_command: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "input_path": self.input_path,
            "input_kind": self.input_kind,
            "pages": self.pages,
            "frames": self.frames,
            "duration": round(self.duration, 6),
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "output_path": self.output_path,
            "work_dir": self.work_dir,
            "source_strip_path": self.source_strip_path,
            "ffmpeg_command": self.ffmpeg_command,
        }


class OfflineRenderError(RuntimeError):
    """Raised when offline rendering cannot be configured or completed."""


def render_offline_video(
    *,
    input_path: Path,
    timeline_path: Path,
    output_path: Path,
    work_dir: Path | None = None,
    ffmpeg: str = "ffmpeg",
    pdf_renderer: str | None = None,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    page_gap: int = 40,
    pdf_dpi: int = 180,
    background: str = "#ffffff",
    keep_frames: bool = False,
    encode_timeout: float = 300.0,
) -> OfflineRenderResult:
    """Render an offline smooth-scroll MP4 from a PDF or PNG pages.

    Args:
        input_path: PDF file, PNG file, PNG directory, or PNG glob pattern.
        timeline_path: Timeline JSON whose segment timings drive scroll speed.
        output_path: MP4 path to write through FFmpeg.
        work_dir: Directory for generated pages, strip image, and frames.
        ffmpeg: FFmpeg executable or command prefix.
        pdf_renderer: Optional pdftoppm-compatible executable or command prefix.
        fps: Output frames per second. Defaults to 30.
        width: Output video width. Defaults to 1920.
        height: Output video height. Defaults to 1080.
        page_gap: Vertical pixels inserted between pages.
        pdf_dpi: Rasterization DPI for PDF inputs.
        background: CSS-style background color used behind pages.
        keep_frames: Keep intermediate frame PNGs after encoding.
        encode_timeout: FFmpeg timeout in seconds.
    """

    _require_pillow()
    if fps <= 0:
        raise OfflineRenderError("fps must be greater than zero")
    if width <= 0 or height <= 0:
        raise OfflineRenderError("width and height must be greater than zero")
    if page_gap < 0:
        raise OfflineRenderError("page_gap cannot be negative")

    timeline = _load_timeline(timeline_path)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = _prepare_work_dir(work_dir, output_path)
    pages, input_kind = _resolve_input_pages(
        input_path=input_path,
        work_dir=work_dir,
        pdf_renderer=pdf_renderer,
        pdf_dpi=pdf_dpi,
    )
    strip_path = work_dir / "source_strip.png"
    strip = _build_vertical_strip(
        pages,
        width=width,
        min_height=height,
        page_gap=page_gap,
        background=background,
    )
    strip.save(strip_path)

    frames_dir = work_dir / "frames"
    _reset_generated_dir(frames_dir)
    frame_count = max(1, int(math.ceil(timeline.duration * fps)))
    _write_scroll_frames(
        strip=strip,
        timeline=timeline,
        frames_dir=frames_dir,
        frame_count=frame_count,
        fps=fps,
        width=width,
        height=height,
    )
    ffmpeg_command = _encode_frames(
        frames_dir=frames_dir,
        output_path=output_path,
        ffmpeg=ffmpeg,
        fps=fps,
        width=width,
        height=height,
        timeout=encode_timeout,
    )
    if not keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)

    return OfflineRenderResult(
        mode="offline_render",
        input_path=str(input_path),
        input_kind=input_kind,
        pages=len(pages),
        frames=frame_count,
        duration=timeline.duration,
        width=width,
        height=height,
        fps=fps,
        output_path=str(output_path),
        work_dir=str(work_dir),
        source_strip_path=str(strip_path),
        ffmpeg_command=ffmpeg_command,
    )


def _require_pillow() -> None:
    if Image is None or ImageColor is None or ImageOps is None:
        raise OfflineRenderError("offline_render requires Pillow; install the muxdev package dependencies")


def _prepare_work_dir(work_dir: Path | None, output_path: Path) -> Path:
    resolved = (work_dir or output_path.parent / ".offline_render" / output_path.stem).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _load_timeline(path: Path) -> RenderTimeline:
    if not path.exists():
        raise OfflineRenderError(f"timeline not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OfflineRenderError(f"invalid timeline JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise OfflineRenderError("timeline must be a JSON object")

    segments: list[TimelineSegment] = []
    for index, raw_segment in enumerate(payload.get("segments") or [], start=1):
        if not isinstance(raw_segment, dict):
            continue
        if str(raw_segment.get("status", "")).lower() == "failed":
            continue
        start = _float_or_none(raw_segment.get("start"))
        end = _float_or_none(raw_segment.get("end"))
        duration = _float_or_none(raw_segment.get("duration"))
        if start is None and duration is None:
            continue
        if start is None:
            start = segments[-1].end if segments else 0.0
        if end is None and duration is not None:
            end = start + duration
        if end is None or end <= start:
            continue
        segments.append(TimelineSegment(index=index, start=start, end=end))

    total_duration = _float_or_none(payload.get("total_duration"))
    max_segment_end = max((segment.end for segment in segments), default=0.0)
    duration = max(total_duration or 0.0, max_segment_end)
    if duration <= 0:
        raise OfflineRenderError("timeline duration must be greater than zero")
    if not segments:
        segments = [TimelineSegment(index=1, start=0.0, end=duration)]
    return RenderTimeline(duration=duration, segments=segments)


def _float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _resolve_input_pages(
    *,
    input_path: Path,
    work_dir: Path,
    pdf_renderer: str | None,
    pdf_dpi: int,
) -> tuple[list[Path], InputKind]:
    path_text = str(input_path)
    if any(char in path_text for char in ("*", "?")):
        pages = sorted(Path(item).resolve() for item in glob.glob(path_text) if Path(item).suffix.lower() == ".png")
        if not pages:
            raise OfflineRenderError(f"PNG glob matched no files: {input_path}")
        return pages, "png"

    input_path = input_path.resolve()
    suffix = input_path.suffix.lower()
    if input_path.is_dir():
        pages = sorted(path.resolve() for path in input_path.iterdir() if path.is_file() and path.suffix.lower() == ".png")
        if not pages:
            raise OfflineRenderError(f"no PNG files found in directory: {input_path}")
        return pages, "png"
    if suffix == ".png":
        if not input_path.exists():
            raise OfflineRenderError(f"PNG input not found: {input_path}")
        return [input_path], "png"
    if suffix == ".pdf":
        if not input_path.exists():
            raise OfflineRenderError(f"PDF input not found: {input_path}")
        pages_dir = work_dir / "pdf_pages"
        _reset_generated_dir(pages_dir)
        return _rasterize_pdf(input_path, pages_dir, pdf_renderer=pdf_renderer, dpi=pdf_dpi), "pdf"
    raise OfflineRenderError("input must be a PDF file, PNG file, PNG directory, or PNG glob pattern")


def _rasterize_pdf(pdf_path: Path, pages_dir: Path, *, pdf_renderer: str | None, dpi: int) -> list[Path]:
    if dpi <= 0:
        raise OfflineRenderError("pdf_dpi must be greater than zero")
    renderer_command = _pdf_renderer_command(pdf_renderer)
    if pdf_renderer and renderer_command is not None:
        return _rasterize_pdf_with_command(pdf_path, pages_dir, renderer_command=renderer_command, dpi=dpi)
    pages = _rasterize_pdf_with_pypdfium2(pdf_path, pages_dir, dpi=dpi)
    if pages:
        return pages
    if renderer_command is None:
        raise OfflineRenderError("PDF input requires pypdfium2 or a pdftoppm-compatible --pdf-renderer command")
    return _rasterize_pdf_with_command(pdf_path, pages_dir, renderer_command=renderer_command, dpi=dpi)


def _rasterize_pdf_with_command(pdf_path: Path, pages_dir: Path, *, renderer_command: list[str], dpi: int) -> list[Path]:
    prefix = pages_dir / "page"
    completed = subprocess.run(
        [
            *renderer_command,
            "-r",
            str(dpi),
            "-png",
            str(pdf_path),
            str(prefix),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise OfflineRenderError(f"PDF rasterizer failed: {detail or completed.returncode}")
    pages = sorted(pages_dir.glob("page-*.png"))
    if not pages:
        raise OfflineRenderError("PDF rasterizer did not produce PNG pages")
    return pages


def _rasterize_pdf_with_pypdfium2(pdf_path: Path, pages_dir: Path, *, dpi: int) -> list[Path]:
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return []
    scale = dpi / 72.0
    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        return []
    pages: list[Path] = []
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil().convert("RGB")
                output = pages_dir / f"page-{index + 1:03d}.png"
                image.save(output)
                pages.append(output)
            finally:
                close = getattr(page, "close", None)
                if close:
                    close()
    finally:
        close = getattr(document, "close", None)
        if close:
            close()
    return pages


def _pdf_renderer_command(pdf_renderer: str | None) -> list[str] | None:
    if pdf_renderer:
        return split_command_line(pdf_renderer)
    bundled_root = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies"
    bundled_exe = bundled_root / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    if bundled_exe.exists():
        return [str(bundled_exe)]
    found = shutil.which("pdftoppm") or shutil.which("pdftoppm.cmd")
    if found:
        return [found]
    bundled = bundled_root / "bin"
    for name in ("pdftoppm", "pdftoppm.cmd"):
        candidate = bundled / name
        if candidate.exists():
            return [str(candidate)]
    return None


def _build_vertical_strip(
    pages: list[Path],
    *,
    width: int,
    min_height: int,
    page_gap: int,
    background: str,
):
    assert Image is not None
    bg_rgb = ImageColor.getrgb(background)  # type: ignore[union-attr]
    scaled_pages = [_load_page_scaled_to_width(path, width=width, background=bg_rgb) for path in pages]
    strip_height = sum(page.height for page in scaled_pages) + page_gap * max(0, len(scaled_pages) - 1)
    strip_height = max(strip_height, min_height)
    strip = Image.new("RGB", (width, strip_height), bg_rgb)
    cursor = 0
    for page in scaled_pages:
        strip.paste(page, (0, cursor))
        cursor += page.height + page_gap
    return strip


def _load_page_scaled_to_width(path: Path, *, width: int, background: tuple[int, int, int]):
    assert Image is not None and ImageOps is not None
    with Image.open(path) as image:
        flattened = ImageOps.exif_transpose(image).convert("RGBA")
        scale = width / flattened.width
        target_height = max(1, int(round(flattened.height * scale)))
        resized = flattened.resize((width, target_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", resized.size, (*background, 255))
        canvas.alpha_composite(resized)
        return canvas.convert("RGB")


def _write_scroll_frames(
    *,
    strip,
    timeline: RenderTimeline,
    frames_dir: Path,
    frame_count: int,
    fps: int,
    width: int,
    height: int,
) -> None:
    max_scroll = max(0, strip.height - height)
    for frame_number in range(1, frame_count + 1):
        timestamp = min(timeline.duration, (frame_number - 1) / fps)
        y = int(round(_scroll_offset_at(timestamp, timeline, max_scroll=max_scroll)))
        frame = strip.crop((0, y, width, y + height))
        if frame.size != (width, height):
            padded = Image.new("RGB", (width, height), (255, 255, 255))  # type: ignore[union-attr]
            padded.paste(frame, (0, 0))
            frame = padded
        frame.save(frames_dir / f"frame_{frame_number:06d}.png")


def _scroll_offset_at(timestamp: float, timeline: RenderTimeline, *, max_scroll: int) -> float:
    if max_scroll <= 0:
        return 0.0
    segment_count = len(timeline.segments)
    if segment_count == 1:
        local = _clamp(timestamp / timeline.duration if timeline.duration else 1.0)
        return _smoothstep(local) * max_scroll
    for ordinal, segment in enumerate(timeline.segments):
        if timestamp <= segment.end or ordinal == segment_count - 1:
            local = _clamp((timestamp - segment.start) / (segment.end - segment.start))
            start_progress = ordinal / segment_count
            end_progress = (ordinal + 1) / segment_count
            progress = start_progress + (end_progress - start_progress) * _smoothstep(local)
            return progress * max_scroll
    return float(max_scroll)


def _smoothstep(value: float) -> float:
    value = _clamp(value)
    return value * value * (3.0 - 2.0 * value)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _encode_frames(
    *,
    frames_dir: Path,
    output_path: Path,
    ffmpeg: str,
    fps: int,
    width: int,
    height: int,
    timeout: float,
) -> list[str]:
    frame_pattern = frames_dir / "frame_%06d.png"
    command = [
        *split_command_line(ffmpeg),
        "-y",
        "-framerate",
        str(fps),
        "-start_number",
        "1",
        "-i",
        str(frame_pattern),
        "-vf",
        f"scale={width}:{height}:flags=lanczos,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
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
        raise OfflineRenderError(f"FFmpeg encode failed: {detail or completed.returncode}")
    if not output_path.exists():
        raise OfflineRenderError(f"FFmpeg did not create expected MP4: {output_path}")
    return command


def _reset_generated_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
