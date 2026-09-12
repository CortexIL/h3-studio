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
OFFERED: tuple[str, ...] = ("i2v", "t2v", "flf2v")

#: Legal in the database, never offered again. r2v was accepted for months with no
#: workflow template, so every r2v job ever queued failed at render; it survives
#: here only so the rows that carry it stay valid. Rendering it needs a second
#: 21GB diffusion model, which is why it is not coming back.
RETIRED: tuple[str, ...] = ("r2v",)

#: Modes whose plumbing lands before the workflow that renders them. Legal in the
#: database so the constraint is widened once rather than per phase, and kept out
#: of OFFERED until they work - an offered mode that cannot render is a job the
#: owner pays to watch fail.
PLANNED: tuple[str, ...] = ("extend",)

#: Everything the jobs.mode CHECK constraint allows. Migration 007 must agree.
KNOWN: tuple[str, ...] = OFFERED + RETIRED + PLANNED

#: Which inputs of the H3 node each mode's references feed, in the order they are
#: stored on the job row. Position is the only thing that distinguishes a start
#: frame from an end frame - upload keys are random hex, so nothing else can.
REF_SLOTS: dict[str, tuple[str, ...]] = {
    "t2v": (),
    "i2v": ("first_frame",),
    "flf2v": ("first_frame", "last_frame"),
    "extend": ("video",),
}

#: Modes that cannot render without exactly this many references. Counted *after*
#: the caller's own keys have been filtered, which is the point: owned_keys() drops
#: a key belonging to someone else rather than refusing it, so a start-to-end job
#: sent with another person's end frame would otherwise arrive with one image and
#: bind it as the start frame.
REQUIRED_REFS: dict[str, int] = {"flf2v": 2, "extend": 1}

REF_ERRORS: dict[str, str] = {
    "flf2v": "start to end needs two images: a start frame and an end frame",
    "extend": "extend needs one video to continue",
}

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
