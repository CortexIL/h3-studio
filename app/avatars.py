"""Turning an uploaded file into a profile picture.

The bytes are treated as hostile. The file is decoded as an image or refused, a
canvas too large to decode safely is refused before it is decoded, and what gets
stored is a fresh encode - so nothing from the original survives: no EXIF, no
GPS position from a phone, no payload riding along in a polyglot file.
"""
from __future__ import annotations

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

SIZE = 256
# Beyond any phone photo in common use; a file claiming more is a decompression
# bomb, or at best more memory than a portrait is worth.
MAX_PIXELS = 50_000_000
FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


def process(data: bytes) -> bytes:
    """Return a SIZE x SIZE WebP, centre-cropped, or raise ValueError saying why not."""
    if not data:
        raise ValueError("that file is empty")
    try:
        with warnings.catch_warnings():
            # The pixel limit below is the real check; Pillow's own warning is noise.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                if img.format not in FORMATS:
                    raise ValueError("use a JPEG, PNG, WebP or GIF picture")
                if img.width * img.height > MAX_PIXELS:
                    raise ValueError("that picture is too large to process")
                img.seek(0)  # the first frame of an animated GIF
                frame = ImageOps.exif_transpose(img)
                alpha = frame.mode in ("RGBA", "LA") or "transparency" in frame.info
                frame = frame.convert("RGBA" if alpha else "RGB")
                square = ImageOps.fit(frame, (SIZE, SIZE), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                square.save(out, "WEBP", quality=85, method=6)
                return out.getvalue()
    except ValueError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as e:
        raise ValueError("that file isn't a picture this can open") from e
