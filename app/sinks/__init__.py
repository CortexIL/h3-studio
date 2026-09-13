"""Where finished videos go.

One interface, one implementation, and a Storage behind it that is either a
bucket or a directory. The orchestrator hands over bytes and gets back a locator;
it has never needed to know more than that.
"""
from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

# A reference travels to the GPU through RunPod's proxy, which one morning moved
# uploads at 23 KB/s: a 3 MB frame took two minutes, the dispatch timed out three
# times, and the feed said "failed" with no reason. The model never sees more
# than the render size, so a heavy frame is scaled to it (never below it, so
# the centre crop is the same) and sent as a JPEG - a tenth of the bytes.
MAX_REFERENCE_BYTES = 600_000
REFERENCE_JPEG_QUALITY = 92


def fit_reference(data: bytes, name: str, width: int, height: int) -> tuple[bytes, str]:
    """What to upload for one reference: (bytes, filename).

    Light images, videos (an extend's tail) and anything Pillow cannot read
    travel as they are - the GPU gets to decide about those.
    """
    from .. import batch  # noqa: PLC0415 - suffix list lives with the upload parser
    if len(data) <= MAX_REFERENCE_BYTES:
        return data, name
    if PurePosixPath(name).suffix.lower() not in batch.IMAGE_SUFFIXES:
        return data, name
    try:
        from PIL import Image  # noqa: PLC0415
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w > width and h > height:
                scale = max(width / w, height / h)
                im = im.resize((max(width, round(w * scale)), max(height, round(h * scale))),
                               Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=REFERENCE_JPEG_QUALITY, optimize=True)
    except Exception:
        log.warning("could not fit reference %s; sending it as is", name, exc_info=True)
        return data, name
    if out.tell() >= len(data):
        return data, name
    return out.getvalue(), str(PurePosixPath(name).with_suffix(".jpg"))


@runtime_checkable
class OutputSink(Protocol):
    async def put(self, job: dict[str, Any], data: bytes, filename: str) -> str:
        """Persist one finished video; returns a locator (path today, URL later)."""


_SLUG_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SPACE = re.compile(r"[\s_]+", re.UNICODE)


def slugify(text: str, limit: int = 48) -> str:
    """Readable filename from a prompt. Keeps Hebrew and other non-ASCII intact."""
    s = _SLUG_STRIP.sub("", text or "").strip()
    s = _SLUG_SPACE.sub("-", s)
    return s[:limit].strip("-") or "clip"


def strip_audio_bytes(data: bytes) -> bytes:
    """Drop the audio stream, returning new bytes.

    Under a step-distilled LoRA the audio H3 emits is undenoised noise, measured
    at -13.9 dB flat against -34.8 dB for the same prompt at thirty steps.
    `-c copy` remuxes rather than re-encodes, so this costs milliseconds and
    cannot degrade the picture.

    Any failure returns the input untouched: a clip with unwanted audio is a far
    better outcome than no clip, on something the user has already paid for.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot strip audio: ffmpeg is not on PATH")
        return data
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "out.mp4"
        src.write_bytes(data)
        try:
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(src),
                 "-map", "0:v", "-c", "copy", "-an", str(dst)],
                capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("audio strip failed: %s", e)
            return data
        # Trust the file, not the exit code: ffmpeg can return 0 having written
        # nothing usable, and replacing a good clip with an empty one is the one
        # failure that would actually lose work.
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("audio strip produced nothing usable; keeping the original")
            return data
        return dst.read_bytes()


def conform_bytes(data: bytes, width: int, height: int) -> bytes:
    """Scale and centre-crop a clip to exactly width x height, returning new bytes.

    H3 renders only multiples of 32, so a delivery size like 1280x720 comes from
    rendering at the model's native 16:9 size and conforming afterwards: scale
    until the frame covers the target, then crop the few rows that overhang.
    Near-lossless (CRF 16), because this is the copy people edit.

    Any failure returns the input untouched, as with the audio strip: a clip at
    the render size is a far better outcome than no clip.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot conform to %dx%d: ffmpeg is not on PATH", width, height)
        return data
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "out.mp4"
        src.write_bytes(data)
        # A portrait render conforms to the portrait version of the target: the
        # delivery sizes are written landscape, but a 9:16 clip must not be
        # cropped to a 16:9 strip.
        dims = video_dimensions_of(src)
        if dims and (dims[1] > dims[0]) != (height > width):
            width, height = height, width
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={width}:{height},setsar=1")
        try:
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(src),
                 "-map", "0:v", "-map", "0:a?", "-vf", vf,
                 "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)],
                capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("conform to %dx%d failed: %s", width, height, e)
            return data
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("conform to %dx%d produced nothing usable; keeping the render",
                        width, height)
            return data
        return dst.read_bytes()


# How much of the old clip carries into the new one when extending.
#
# MiniMaxH3AddGuide anchors a guide clip on the model's own grid - it snaps a
# length down until `frames % 17 == 5`, giving 5, 22, 39 and so on. 22 frames is
# about nine tenths of a second at 24fps: enough movement for the new clip to
# continue the old one's motion rather than merely start on the same picture,
# and short enough that the overlap costs about a cent of render.
OVERLAP_FRAMES = 22

# A voice track longer than the longest clip is dead weight on the way to the GPU;
# the guide trims to the clip anyway.
MAX_GUIDE_AUDIO_SECONDS = 16
GUIDE_AUDIO_RATE = 48000


def audio_track_bytes(data: bytes, max_seconds: float = MAX_GUIDE_AUDIO_SECONDS) -> bytes | None:
    """Any audio file as a stereo 48 kHz WAV, cut to the clip length, or None.

    One format on the pod, whatever the phone or the editor produced: LoadAudio
    then never meets a codec it lacks on a rented GPU. Like the tail cut, this
    fails loudly - it decides whether a render still to come is right.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot transcode audio: ffmpeg is not on PATH")
        return None
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.bin"
        dst = Path(td) / "track.wav"
        src.write_bytes(data)
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(src), "-vn", "-t", str(max_seconds),
             "-ac", "2", "-ar", str(GUIDE_AUDIO_RATE), "-c:a", "pcm_s16le", str(dst)],
            capture_output=True, timeout=120)
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1000:
            log.warning("audio transcode failed: %s", r.stderr.decode(errors="replace")[:300])
            return None
        return dst.read_bytes()
GUIDE_FPS = 24


def _ffprobe(path: Path, args: list[str]) -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return ""
    try:
        r = subprocess.run([ffprobe, "-v", "error", *args, str(path)],
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.decode("utf-8", "replace").strip()


def tail_clip_bytes(data: bytes, frames: int = OVERLAP_FRAMES,
                    fps: int = GUIDE_FPS) -> bytes | None:
    """The last `frames` frames of a clip, with their sound, at `fps`.

    This is what lets an extended clip continue a movement instead of merely
    starting on the same still: these frames and this audio are anchored at the
    front of the new render, so motion and sound carry across the join.

    Three things here are deliberate.

    It ends on the source's final frame, exactly. AddGuide takes the *first* N
    frames of whatever it is handed, after snapping N down to the model's grid,
    so a loose tail would anchor the wrong window and leave a gap at the seam.

    It is resampled to 24fps, the only rate the model works at. The last 22
    frames of a 30fps phone clip span three quarters of a second, and the
    continuation would carry on at the wrong speed.

    And unlike every other helper here, a failure returns None rather than the
    input. The others protect a clip that has already been rendered and paid
    for; this one decides whether a render that has not happened yet will be
    right, and a wrong tail is an invisibly broken join.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot cut a tail clip: ffmpeg is not on PATH")
        return None
    span = frames / float(fps)
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "tail.mp4"
        src.write_bytes(data)

        raw = _ffprobe(src, ["-show_entries", "format=duration", "-of", "csv=p=0"])
        try:
            duration = float(raw.split(",")[0])
        except (TypeError, ValueError):
            log.warning("cannot cut a tail clip: the source has no readable duration")
            return None
        if duration <= 0:
            return None
        start = max(0.0, duration - span)

        has_audio = bool(_ffprobe(src, ["-select_streams", "a:0", "-show_entries",
                                        "stream=index", "-of", "csv=p=0"]))
        cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src),
               "-ss", f"{start:.3f}", "-vf", f"fps={fps}", "-frames:v", str(frames),
               "-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        cmd += (["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"] if has_audio else ["-an"])
        cmd.append(str(dst))
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=300,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("tail cut failed: %s", e)
            return None
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("tail cut produced nothing usable: %s",
                        r.stderr.decode("utf-8", "replace")[:200])
            return None
        if has_audio:
            return dst.read_bytes()

        # A silent source gets silence laid over the cut, rather than during it:
        # generated silence cannot be seeked into the way a real track can, and
        # trying turns the whole cut into an empty file. H3 reads the guide's
        # audio, and an absent track is a crash on the pod, not a quiet omission.
        return _with_silence(ffmpeg, dst, Path(td) / "tail-silent.mp4")


def _with_silence(ffmpeg: str, src: Path, dst: Path) -> bytes | None:
    """The same clip with a silent audio track. The picture is copied, not re-encoded."""
    try:
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(src),
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "128k", "-shortest",
             "-movflags", "+faststart", str(dst)],
            capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("could not add a silent track to the tail clip: %s", e)
        return None
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
        log.warning("could not add a silent track to the tail clip")
        return None
    return dst.read_bytes()


MAX_REFERENCE_SECONDS = 15
REFERENCE_SHORT_EDGE = 768


def reference_video_bytes(data: bytes, max_seconds: float = MAX_REFERENCE_SECONDS) -> bytes | None:
    """A reference video as the model will read it: 768 short edge, 24 fps, at
    most a clip's length, with a soundtrack (silence if it had none). None when
    the file cannot be read - like the tail cut, a bad reference decides a render
    still to come, so it fails here rather than on the GPU."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot prepare a reference video: ffmpeg is not on PATH")
        return None
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.bin"
        dst = Path(td) / "ref.mp4"
        src.write_bytes(data)
        scale = (f"scale='if(gt(iw,ih),-2,{REFERENCE_SHORT_EDGE})':"
                 f"'if(gt(iw,ih),{REFERENCE_SHORT_EDGE},-2)',fps=24")
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(src), "-t", str(max_seconds),
             "-map", "0:v:0", "-map", "0:a?", "-vf", scale, "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(dst)],
            capture_output=True, timeout=300)
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1000:
            log.warning("reference video encode failed: %s", r.stderr.decode(errors="replace")[:300])
            return None
        if _ffprobe(dst, ["-select_streams", "a:0", "-show_entries", "stream=index",
                          "-of", "csv=p=0"]):
            return dst.read_bytes()
        return _with_silence(ffmpeg, dst, Path(td) / "ref-silent.mp4")


def video_dimensions_of(path: Path) -> tuple[int, int] | None:
    """Width and height of a clip on disk, or None when ffprobe cannot say."""
    out = _ffprobe(path, ["-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0"])
    try:
        w, h = (int(x) for x in out.split(",")[:2])
    except ValueError:
        return None
    return (w, h) if w > 0 and h > 0 else None


def video_frame_count(data: bytes) -> int | None:
    """How many frames a clip has, or None when it cannot be read."""
    if not shutil.which("ffprobe"):
        return None
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "probe.mp4"
        src.write_bytes(data)
        try:
            out = _ffprobe(src, ["-select_streams", "v:0", "-count_packets",
                                 "-show_entries", "stream=nb_read_packets",
                                 "-of", "default=noprint_wrappers=1:nokey=1"])
            return int(out) if out else None
        except Exception:
            return None


def drop_leading_frames(data: bytes, frames: int = OVERLAP_FRAMES,
                        fps: int = GUIDE_FPS) -> bytes:
    """Remove the first `frames` frames and their audio, returning new bytes.

    An extended clip opens by reproducing the tail it was asked to continue - that
    reproduction is what makes the movement carry across the join. Once it has
    served that purpose it is duplicate footage, so it comes off here and the two
    clips lie end to end with nothing to trim by hand. That is the whole promise
    of the mode.

    Any failure returns the input untouched: an extra second at the front of a
    clip that has already been rendered and paid for is a far better outcome than
    no clip at all.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot trim the overlap: ffmpeg is not on PATH")
        return data
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "out.mp4"
        src.write_bytes(data)
        try:
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(src),
                 "-ss", f"{frames / float(fps):.4f}",
                 "-map", "0:v", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)],
                capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("trimming the overlap failed: %s", e)
            return data
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("trimming the overlap produced nothing usable; keeping the render")
            return data
        return dst.read_bytes()


def extract_poster(data: bytes, width: int = 640) -> bytes | None:
    """One JPEG frame from a clip, or None.

    Half a second in rather than frame zero, which is often a black or
    half-formed first frame. Falls back to frame zero for clips shorter than
    that. Any failure returns None: a clip without a poster is fine, a clip
    that failed because its poster did would not be.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "poster.jpg"
        src.write_bytes(data)
        for seek in ("0.5", "0"):
            try:
                subprocess.run(
                    [ffmpeg, "-v", "error", "-y", "-ss", seek, "-i", str(src),
                     "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)],
                    capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                return None
            if dst.exists() and dst.stat().st_size > 0:
                out = dst.read_bytes()
                return out if out[:2] == b"\xff\xd8" else None
    return None


def make_sink(settings: Any, generation: Any = None) -> OutputSink:
    """Where finished clips go. One implementation; the storage behind it varies."""
    from .. import storage as storage_mod
    from .object_store import ObjectSink
    return ObjectSink(storage_mod.get_storage(), keep_audio=settings.keep_audio,
                      presets=getattr(generation, "presets", None))
