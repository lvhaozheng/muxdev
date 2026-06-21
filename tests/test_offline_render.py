from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from muxdev.cli import app
from muxdev.services.offline_render import render_offline_video


runner = CliRunner()


def test_offline_render_png_directory_generates_timeline_scrolled_frames() -> None:
    temp_dir = _workspace_temp()
    try:
        pages = _write_png_pages(temp_dir)
        timeline = _write_timeline(temp_dir / "timeline.json", duration=1.0)
        fake_ffmpeg = _write_fake_ffmpeg(temp_dir)

        result = render_offline_video(
            input_path=pages,
            timeline_path=timeline,
            output_path=temp_dir / "out.mp4",
            work_dir=temp_dir / "render",
            ffmpeg=f"{sys.executable} {fake_ffmpeg}",
            fps=4,
            width=320,
            height=180,
            keep_frames=True,
        )
        payload = result.to_dict()
        first_frame = Image.open(temp_dir / "render" / "frames" / "frame_000001.png")
        last_frame = Image.open(temp_dir / "render" / "frames" / "frame_000004.png")

        assert payload["mode"] == "offline_render"
        assert payload["input_kind"] == "png"
        assert payload["pages"] == 2
        assert payload["frames"] == 4
        assert payload["width"] == 320
        assert payload["height"] == 180
        assert (temp_dir / "out.mp4").exists()
        assert first_frame.getpixel((160, 90)) != last_frame.getpixel((160, 90))
        assert "-framerate" in payload["ffmpeg_command"]
        assert "scale=320:180:flags=lanczos,format=yuv420p" in payload["ffmpeg_command"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_offline_render_pdf_uses_pdftoppm_style_renderer() -> None:
    temp_dir = _workspace_temp()
    try:
        pdf_path = temp_dir / "slides.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% fake input for renderer test\n")
        timeline = _write_timeline(temp_dir / "timeline.json", duration=0.5)
        fake_ffmpeg = _write_fake_ffmpeg(temp_dir)
        fake_pdf_renderer = _write_fake_pdf_renderer(temp_dir)

        result = render_offline_video(
            input_path=pdf_path,
            timeline_path=timeline,
            output_path=temp_dir / "pdf.mp4",
            work_dir=temp_dir / "render",
            ffmpeg=f"{sys.executable} {fake_ffmpeg}",
            pdf_renderer=f"{sys.executable} {fake_pdf_renderer}",
            fps=2,
            width=200,
            height=120,
        )

        assert result.input_kind == "pdf"
        assert result.pages == 2
        assert result.frames == 1
        assert (temp_dir / "pdf.mp4").exists()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_offline_render_cli_outputs_json() -> None:
    temp_dir = _workspace_temp()
    try:
        with _chdir(temp_dir):
            pages = _write_png_pages(temp_dir)
            timeline = _write_timeline(Path("timeline.json"), duration=0.5)
            fake_ffmpeg = _write_fake_ffmpeg(temp_dir)

            result = runner.invoke(
                app,
                [
                    "offline-render",
                    "run",
                    "--input",
                    str(pages),
                    "--timeline",
                    str(timeline),
                    "--output",
                    "cli.mp4",
                    "--work-dir",
                    "render",
                    "--ffmpeg",
                    f"{sys.executable} {fake_ffmpeg}",
                    "--fps",
                    "2",
                    "--width",
                    "160",
                    "--height",
                    "90",
                    "--json",
                ],
            )
            payload = json.loads(result.stdout)

            assert result.exit_code == 0
            assert payload["input_kind"] == "png"
            assert payload["fps"] == 2
            assert payload["width"] == 160
            assert payload["height"] == 90
            assert Path(payload["output_path"]).name == "cli.mp4"
            assert Path("cli.mp4").exists()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _write_png_pages(root: Path) -> Path:
    pages = root / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    first = Image.new("RGB", (320, 300), (220, 40, 40))
    second = Image.new("RGB", (320, 300), (40, 80, 220))
    first.save(pages / "001.png")
    second.save(pages / "002.png")
    return pages


def _write_timeline(path: Path, *, duration: float) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "total_duration": duration,
                "segments": [
                    {"index": 1, "start": 0.0, "end": duration / 2, "duration": duration / 2, "status": "generated"},
                    {"index": 2, "start": duration / 2, "end": duration, "duration": duration / 2, "status": "generated"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_fake_ffmpeg(root: Path) -> Path:
    script = root / "fake_ffmpeg.py"
    script.write_text(
        """
from __future__ import annotations

import sys
from pathlib import Path

Path(sys.argv[-1]).write_bytes(b"fake mp4")
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _write_fake_pdf_renderer(root: Path) -> Path:
    script = root / "fake_pdftoppm.py"
    script.write_text(
        """
from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image

prefix = Path(sys.argv[-1])
Image.new("RGB", (240, 240), (230, 230, 230)).save(prefix.with_name(prefix.name + "-1.png"))
Image.new("RGB", (240, 240), (60, 90, 180)).save(prefix.with_name(prefix.name + "-2.png"))
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _workspace_temp() -> Path:
    path = Path(".test_workspaces") / f"offline_{uuid.uuid4().hex}"
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
