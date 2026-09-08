"""Watched folder: images AND whole batches arriving from other applications.

This is the lowest-common-denominator integration: anything that can save a file can
hand work to H3 Studio. ChatGPT, a screenshot tool, Explorer, a script - none of them
need to know this app exists. Claude gets a richer path through the MCP server
(app/mcp_server.py), which ultimately writes here too.

Two kinds of file are recognised:

  * images           -> attached as reference images
  * .txt / .json     -> a batch: prompts, and optionally which image each one uses

The batch format exists because the common case is not "here is an image" but "here
are twelve prompts and the pictures that go with them", exported in one go from
somewhere else. Without it the images arrive automatically and the prompts still have
to be copied by hand, which is the slow half.

Files are moved to _used/ once taken, so nothing is picked up twice and the folder
stays a genuine inbox rather than an ever-growing pile.

SECURITY: batch files come from outside this app and are treated as data, never as
instructions. Image references are restricted to bare filenames resolved inside the
inbox - a batch cannot reach elsewhere on disk - and every field is validated and
clamped rather than trusted.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

log = logging.getLogger("h3studio.inbox")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
BATCH_SUFFIXES = {".txt", ".json"}
ARCHIVE_SUFFIXES = {".zip"}

# Zip limits. An archive here comes from outside the app, so it gets treated as
# hostile input even when it isn't: no path traversal, no absolute paths, no
# subfolders, and hard caps so a decompression bomb cannot fill the disk.
MAX_ZIP_ENTRIES = 400
MAX_ZIP_UNCOMPRESSED = 2 * 1024 * 1024 * 1024      # 2 GB
MAX_ZIP_RATIO = 200                                # uncompressed / compressed

# A file that changed within this window may still be mid-write by the other app.
# Picking it up then yields a truncated file, which fails much later and confusingly.
SETTLE_SECONDS = 1.5

# A batch naming images that have not arrived yet is held back rather than queued
# without them - downloads land in an arbitrary order, and a batch that renders
# without its references is worse than one that waits a moment.
#
# Kept short on purpose: the wait exists for files landing seconds apart, but the
# other cause of a missing image is a typo in the batch, and holding everything else
# hostage to that for a long time just looks like the app has stopped.
BATCH_WAIT_SECONDS = 20

MAX_JOBS_PER_BATCH = 200
VALID_PRESETS = {"draft", "final"}
VALID_MODES = {"t2v", "i2v", "r2v"}

SKIP_NAMES = {"read me.txt", "readme.txt"}


class Inbox:
    def __init__(self, folder: Path, uploads: Path) -> None:
        self.folder = Path(folder)
        self.uploads = Path(uploads)
        self.used = self.folder / "_used"
        self._seen: set[str] = set()
        self._waiting: dict[str, float] = {}     # batch file -> first time we saw it
        self._lock = threading.Lock()

    def ensure(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.used.mkdir(parents=True, exist_ok=True)
        self.uploads.mkdir(parents=True, exist_ok=True)
        readme = self.folder / "READ ME.txt"
        if not readme.exists():
            readme.write_text(
                "H3 Studio - inbox\n"
                "=================\n\n"
                "Drop files here and H3 Studio picks them up within a couple of seconds.\n"
                "Dragging them onto the H3 Studio window does exactly the same thing.\n"
                "H3 Studio has to be running either way - a file dropped while it is\n"
                "closed just waits here until you start it.\n"
                "Taken files move to _used/ so nothing is processed twice.\n\n"
                "IMAGES (.png .jpg .webp ...)\n"
                "  Attached as reference images.\n\n"
                "PROMPTS (.txt)\n"
                "  One prompt per line. Blank lines and lines starting with # are ignored.\n\n"
                "BATCH (.json)\n"
                "  {\n"
                '    "defaults": { "seconds": 10, "preset": "draft", "mode": "t2v" },\n'
                '    "jobs": [\n'
                '      { "prompt": "a cat on the moon", "image": "cat.png", "seconds": 8 },\n'
                '      { "prompt": "waves at sunset", "takes": 2 }\n'
                "    ]\n"
                "  }\n\n"
                "  Only 'prompt' is required. 'image' must be a plain filename dropped into\n"
                "  this same folder. A batch waits up to 20 seconds for its images before\n"
                "  running without them.\n",
                encoding="utf-8",
            )

    # ---------- scanning ----------

    def scan(self) -> dict[str, list]:
        """Take newly arrived files. Each file is reported exactly once.

        Callers that need to show these to a polling client must keep their own
        short display window - see _RecentEvents in main.py. Reporting them repeatedly
        from here would re-queue the same batch on every poll.

        Serialised, because it is called from a thread pool on every status poll and
        two overlapping polls could both pass the `already seen` check before either
        recorded the file - which really did queue one batch twice, turning three jobs
        into six. At real GPU prices that is not a cosmetic bug.
        """
        with self._lock:
            return self._scan_locked()

    def _scan_locked(self) -> dict[str, list]:
        if not self.folder.exists():
            return {"images": [], "batches": [], "waiting_for_images": []}

        now = time.time()
        images: list[dict[str, Any]] = []
        batch_files: list[Path] = []

        for src in sorted(self.folder.iterdir()):
            if src.is_dir() or src.name.lower() in SKIP_NAMES:
                continue
            suffix = src.suffix.lower()
            if suffix not in IMAGE_SUFFIXES and suffix not in BATCH_SUFFIXES                     and suffix not in ARCHIVE_SUFFIXES:
                continue
            try:
                if now - src.stat().st_mtime < SETTLE_SECONDS:
                    continue                  # still being written; catch it next tick
            except OSError:
                continue
            if suffix in ARCHIVE_SUFFIXES:
                # Unpacked in place; its contents are picked up on the next scan,
                # which keeps one code path for loose files and archived ones alike.
                self._unpack(src)
            elif suffix in BATCH_SUFFIXES:
                batch_files.append(src)       # handled after images, see below
            else:
                taken = self._take_image(src)
                if taken:
                    images.append(taken)

        # Images first, deliberately: a batch that names an image which arrived in the
        # same tick should find it already available.
        batches = [b for src in batch_files if (b := self._take_batch(src, now))]

        # An image a batch already claimed must not also be offered as a loose
        # reference. A zip of eleven frames plus its batch file would otherwise
        # attach all eleven to the compose box as well, and they would silently
        # ride along on whatever the user typed next.
        claimed = {
            ref
            for b in batches
            for job in b.get("jobs", [])
            for ref in job.get("ref_images", [])
        }
        loose = [i for i in images if i["path"] not in claimed]

        return {"images": loose, "batches": batches,
                "waiting_for_images": self._waiting_summary()}

    def _waiting_summary(self) -> list[dict[str, Any]]:
        """Batches held back because an image they name has not arrived yet."""
        return [{"source": name, "waited_s": round(time.time() - since, 1)}
                for name, since in self._waiting.items()]

    def _unpack(self, src: Path) -> None:
        """Flatten a zip into the inbox so its contents are picked up normally."""
        try:
            key = f"{src.name}:{src.stat().st_mtime_ns}"
        except OSError:
            return
        if key in self._seen:
            return
        self._seen.add(key)

        allowed = IMAGE_SUFFIXES | BATCH_SUFFIXES
        try:
            with zipfile.ZipFile(src) as z:
                infos = z.infolist()
                if len(infos) > MAX_ZIP_ENTRIES:
                    log.warning("inbox: %s has %d entries, refusing", src.name, len(infos))
                    self._retire(src)
                    return
                total = sum(i.file_size for i in infos)
                packed = max(1, sum(i.compress_size for i in infos))
                if total > MAX_ZIP_UNCOMPRESSED or total / packed > MAX_ZIP_RATIO:
                    log.warning("inbox: %s looks like a zip bomb, refusing", src.name)
                    self._retire(src)
                    return

                taken = 0
                for info in infos:
                    if info.is_dir():
                        continue
                    # Flatten: only the basename is used, so "../" and absolute
                    # paths in the archive cannot escape the inbox.
                    name = Path(info.filename.replace("\\", "/")).name
                    if not name or name.startswith("."):
                        continue
                    if Path(name).suffix.lower() not in allowed:
                        continue
                    dest = self._unique(self.folder / name)
                    with z.open(info) as fh, dest.open("wb") as out:
                        shutil.copyfileobj(fh, out, 1024 * 1024)
                    taken += 1
        except (zipfile.BadZipFile, OSError) as e:
            log.warning("inbox: cannot read %s (%s)", src.name, e)
            self._retire(src)
            return

        log.info("inbox: unpacked %d file(s) from %s", taken, src.name)
        self._retire(src)

    def _take_image(self, src: Path) -> dict[str, Any] | None:
        try:
            key = f"{src.name}:{src.stat().st_mtime_ns}"
        except OSError:
            return None
        if key in self._seen:
            return None
        try:
            dest = self._unique(self.uploads / src.name)
            shutil.copy2(src, dest)
            shutil.move(str(src), str(self._unique(self.used / src.name)))
        except OSError as e:
            # Usually the other app still holds the handle - retry next scan.
            log.debug("inbox: could not take %s yet (%s)", src.name, e)
            return None
        self._seen.add(key)
        return {"name": src.name, "path": str(dest)}

    def _take_batch(self, src: Path, now: float) -> dict[str, Any] | None:
        try:
            key = f"{src.name}:{src.stat().st_mtime_ns}"
        except OSError:
            return None
        if key in self._seen:
            return None
        try:
            text = src.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            log.warning("inbox: cannot read batch %s (%s)", src.name, e)
            return None

        try:
            jobs = self._parse(text, src.suffix.lower())
        except ValueError as e:
            self._seen.add(key)
            self._retire(src)
            return {"source": src.name, "jobs": [], "error": str(e)}

        if not jobs:
            self._seen.add(key)
            self._retire(src)
            return None

        resolved, missing = self._resolve_images(jobs)
        waited = now - self._waiting.setdefault(src.name, now)
        if missing and waited < BATCH_WAIT_SECONDS:
            return None                       # give the images time to land

        self._waiting.pop(src.name, None)
        self._seen.add(key)
        self._retire(src)
        return {"source": src.name, "jobs": resolved, "missing": sorted(missing)}

    # ---------- parsing ----------

    def _parse(self, text: str, suffix: str) -> list[dict[str, Any]]:
        if suffix == ".json":
            return self._parse_json(text)
        return [{"prompt": line.strip()} for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]

    def _parse_json(self, text: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"not valid JSON: {e}") from e

        # Accept a bare list of jobs, or of plain strings, as well as the documented
        # {"defaults":..., "jobs":[...]} shape. Exporters are inconsistent and there
        # is no reason to reject a form whose intent is unambiguous.
        if isinstance(data, list):
            raw, defaults = data, {}
        elif isinstance(data, dict):
            raw = data.get("jobs") or data.get("prompts") or []
            defaults = data.get("defaults") or {}
            if not isinstance(raw, list):
                raise ValueError("'jobs' must be a list")
            if not isinstance(defaults, dict):
                defaults = {}
        else:
            raise ValueError("expected an object or a list")

        out: list[dict[str, Any]] = []
        for item in raw[:MAX_JOBS_PER_BATCH]:
            if isinstance(item, str):
                item = {"prompt": item}
            if not isinstance(item, dict):
                continue
            merged = {**defaults, **item}
            prompt = str(merged.get("prompt") or "").strip()
            if not prompt:
                continue
            out.append({
                "prompt": prompt[:2000],
                "image": merged.get("image") or merged.get("reference"),
                "seconds": _clamp_int(merged.get("seconds"), 4, 15, 10),
                "takes": _clamp_int(merged.get("takes") or merged.get("count"), 1, 10, 1),
                "preset": (str(merged.get("preset") or "draft").lower()
                           if str(merged.get("preset") or "draft").lower() in VALID_PRESETS
                           else "draft"),
                "mode": (str(merged.get("mode") or "").lower()
                         if str(merged.get("mode") or "").lower() in VALID_MODES else None),
            })
        return out

    def _resolve_images(self, jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
        """Map each job's image name onto a real uploaded file.

        Only bare filenames are honoured, resolved inside the inbox: a batch file is
        untrusted input and must not be able to name a path elsewhere on the machine.
        """
        missing: set[str] = set()
        for job in jobs:
            name = job.pop("image", None)
            job["ref_images"] = []
            if not name:
                continue
            name = str(name)
            if "/" in name or "\\" in name or name.startswith("."):
                missing.add(name)
                continue
            hit = self._find_uploaded(name)
            if hit:
                job["ref_images"] = [str(hit)]
                if not job.get("mode"):
                    job["mode"] = "i2v"
            else:
                missing.add(name)
        for job in jobs:
            job["mode"] = job.get("mode") or "t2v"
        return jobs, missing

    def _find_uploaded(self, name: str) -> Path | None:
        exact = self.uploads / name
        if exact.exists():
            return exact
        # Taken images can be renamed on collision (foo_2.png); match on the stem.
        stem = Path(name).stem.lower()
        for p in self.uploads.iterdir():
            if p.is_file() and (p.stem.lower() == stem or p.stem.lower().startswith(stem + "_")):
                return p
        return None

    # ---------- housekeeping ----------

    def _retire(self, src: Path) -> None:
        try:
            shutil.move(str(src), str(self._unique(self.used / src.name)))
        except OSError as e:
            log.warning("inbox: could not move %s to _used (%s)", src.name, e)

    @staticmethod
    def _unique(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix, n = path.stem, path.suffix, 2
        while True:
            cand = path.with_name(f"{stem}_{n}{suffix}")
            if not cand.exists():
                return cand
            n += 1


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default
