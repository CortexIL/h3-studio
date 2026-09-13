"""A portrait clip conforms to the portrait version of a landscape delivery size."""
import shutil
import subprocess
from pathlib import Path

import pytest

from app.sinks import conform_bytes, video_dimensions_of

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="needs ffmpeg and ffprobe")


def _clip(path: Path, size: str) -> bytes:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"color=c=black:s={size}:d=1:r=8", "-pix_fmt", "yuv420p", str(path)],
                   check=True, stdin=subprocess.DEVNULL)
    return path.read_bytes()


def test_portrait_stays_portrait(tmp_path):
    data = _clip(tmp_path / "p.mp4", "96x192")
    dst = tmp_path / "out.mp4"
    dst.write_bytes(conform_bytes(data, 160, 96))
    assert video_dimensions_of(dst) == (96, 160)


def test_landscape_is_unchanged_by_the_check(tmp_path):
    data = _clip(tmp_path / "l.mp4", "192x96")
    dst = tmp_path / "out.mp4"
    dst.write_bytes(conform_bytes(data, 160, 96))
    assert video_dimensions_of(dst) == (160, 96)
