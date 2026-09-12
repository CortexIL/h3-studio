"""Every render mode, in one place.

The list used to live in four copies that could not see each other: a CHECK
constraint in the first migration, a set in the jobs route, another in the batch
parser, and the template map. Adding a mode meant finding all four, and missing one
failed differently each time - a 400, a silent downgrade to the default, or a job
that queued happily and then died on a rented GPU.

`KNOWN` is what the database permits, and it never shrinks: a mode that has ever
been written to a row must stay legal or that row becomes unreadable. `OFFERED` is
what the API accepts and the picker shows, and it grows only when a mode has a
workflow that can actually render it.

Nothing here imports from the rest of the app, so anything may import it.
"""
from __future__ import annotations

DEFAULT_MODE = "i2v"

#: Accepted from the browser and offered in the picker.
OFFERED: tuple[str, ...] = ("i2v", "t2v")

#: Legal in the database, never offered again. r2v was accepted for months with no
#: workflow template, so every r2v job ever queued failed at render; it survives
#: here only so the rows that carry it stay valid. Rendering it needs a second
#: 21GB diffusion model, which is why it is not coming back.
RETIRED: tuple[str, ...] = ("r2v",)

#: Modes whose plumbing lands before the workflow that renders them. Legal in the
#: database so the constraint is widened once rather than per phase, and kept out
#: of OFFERED until they work - an offered mode that cannot render is a job the
#: owner pays to watch fail.
PLANNED: tuple[str, ...] = ("flf2v", "extend")

#: Everything the jobs.mode CHECK constraint allows. Migration 007 must agree.
KNOWN: tuple[str, ...] = OFFERED + RETIRED + PLANNED

LABELS: dict[str, str] = {
    "i2v": "Reference",
    "t2v": "Text → video",
    "flf2v": "Start to end",
    "extend": "Extend",
    "r2v": "Reference video (retired)",
}


def label(mode: str) -> str:
    """A readable name for any mode, including one this build has never heard of.

    Rolling the app back while a row carries a newer mode must not produce a blank
    label, so an unknown mode reads as itself rather than as nothing.
    """
    return LABELS.get(mode, mode)
