"""The effect presets: community prompt embeddings for MiniMax H3.

Ten small files hosted in the Comfy-Org/MiniMax-H3 repository (contributed by
silveroxides, not made by MiniMax or Comfy). ComfyUI mixes one into the text
conditioning when the prompt contains `embedding:<file name>`; the studio adds
that token for every effect a clip picked.
"""
from __future__ import annotations

EFFECTS: tuple[str, ...] = (
    "art_is_explosion",
    "blooming_flowers",
    "bullet_time",
    "dark_magic",
    "fire_breath",
    "four_seasons",
    "kiss_camera",
    "spiral_ascent",
    "storm_magic",
    "truman_show",
)
MAX_EFFECTS = 3
FILE_PREFIX = "minimaxh3_"


def embedding_file(effect: str) -> str:
    return f"{FILE_PREFIX}{effect}.safetensors"


def prompt_token(effect: str) -> str:
    """What the prompt carries: ComfyUI's `embedding:` syntax with the file's stem."""
    return f"embedding:{FILE_PREFIX}{effect}"


def clean(names: list[str] | tuple[str, ...] | None) -> list[str]:
    """Known effects only, in the order given, without repeats, at most MAX_EFFECTS."""
    out: list[str] = []
    for n in names or ():
        if n in EFFECTS and n not in out:
            out.append(n)
    return out[:MAX_EFFECTS]
