"""Per-clip render controls: the knobs the graph has, editable on top of a preset.

A preset is a saved combination. A clip may override any of these; NULL in the
database means "whatever the preset says", which keeps every clip made before the
controls existed exactly as it was.
"""
from __future__ import annotations

from typing import Any

STEPS = (1, 60)
SHIFT = (0.5, 40.0)
SIZE = (256, 2048)
MULTIPLE = 32
# The experimental ceiling: 1920x1088 is 2.7x the model's trained canvas. Anything
# larger is a memory gamble on a rented GPU, so it is refused rather than tried.
MAX_AREA = 1920 * 1088

# The model's own defaults (MiniMaxH3SigmaShift). A clip that asks for exactly
# these gets no extra node, so the default graph stays byte-identical.
DEFAULT_SHIFT_VIDEO = 12.0
DEFAULT_SHIFT_AUDIO = 3.0

FIELDS = ("steps", "shift_video", "shift_audio", "width", "height")


def validate(steps: int | None, shift_video: float | None, shift_audio: float | None,
             width: int | None, height: int | None) -> None:
    """Raise ValueError with a sentence a person can act on."""
    if steps is not None and not STEPS[0] <= steps <= STEPS[1]:
        raise ValueError(f"steps must be between {STEPS[0]} and {STEPS[1]}")
    for name, value in (("motion", shift_video), ("audio shift", shift_audio)):
        if value is not None and not SHIFT[0] <= value <= SHIFT[1]:
            raise ValueError(f"{name} must be between {SHIFT[0]} and {SHIFT[1]}")
    if (width is None) != (height is None):
        raise ValueError("width and height go together")
    if width is not None and height is not None:
        for name, value in (("width", width), ("height", height)):
            if not SIZE[0] <= value <= SIZE[1]:
                raise ValueError(f"{name} must be between {SIZE[0]} and {SIZE[1]}")
            if value % MULTIPLE:
                raise ValueError(f"{name} must be a multiple of {MULTIPLE}")
        if width * height > MAX_AREA:
            raise ValueError("at most 1920x1088 (2.1 megapixels) - larger is untested "
                             "and may run out of GPU memory")


def effective(preset: Any, job: dict[str, Any]) -> Any:
    """The preset with the clip's overrides applied. Missing overrides change nothing."""
    update: dict[str, Any] = {}
    if job.get("steps"):
        update["steps"] = int(job["steps"])
    if job.get("width") and job.get("height"):
        update["width"] = int(job["width"])
        update["height"] = int(job["height"])
        # A conformed output size belongs to the preset's own render size.
        update["output_width"] = 0
        update["output_height"] = 0
    return preset.model_copy(update=update) if update else preset


def shifts(job: dict[str, Any], preset: Any = None) -> tuple[float, float] | None:
    """(video, audio) flow shifts when the clip - or, failing that, its preset -
    asks for something other than the model's defaults, else None."""
    video = float(job.get("shift_video") or getattr(preset, "shift_video", None)
                  or DEFAULT_SHIFT_VIDEO)
    audio = float(job.get("shift_audio") or getattr(preset, "shift_audio", None)
                  or DEFAULT_SHIFT_AUDIO)
    if video == DEFAULT_SHIFT_VIDEO and audio == DEFAULT_SHIFT_AUDIO:
        return None
    return video, audio
