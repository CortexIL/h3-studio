from __future__ import annotations

import io
import json
import shutil
import subprocess
import wave
import zipfile

import pytest

from app import batch
from app.sinks import MAX_GUIDE_AUDIO_SECONDS

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone(seconds: float = 2, fmt: str = "wav", codec: str = "pcm_s16le") -> bytes:
    """A real audio file, because the transcode is the thing being tested."""
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-ac", "2",
         "-c:a", codec, "-f", fmt, "-"], check=True, capture_output=True).stdout


def test_plain_text_is_one_prompt_per_line():
    jobs = batch.parse("first\n\n# a comment\nsecond\n", ".txt")
    assert [j["prompt"] for j in jobs] == ["first", "second"]


def test_text_jobs_get_defaults():
    j = batch.parse("only\n", ".txt")[0]
    assert j["takes"] == 1 and j["seconds"] == 10 and j["ref_names"] == []


def test_json_object_shape():
    text = json.dumps({"defaults": {"seconds": 6, "preset": "turbo"},
                       "jobs": [{"prompt": "a", "takes": 2},
                                {"prompt": "b", "seconds": 12}]})
    a, b = batch.parse(text, ".json")
    assert (a["prompt"], a["takes"], a["seconds"], a["preset"]) == ("a", 2, 6, "turbo")
    assert b["seconds"] == 12


def test_turbo_is_no_longer_silently_downgraded():
    """The watcher validated against a stale {draft, final} and rewrote turbo."""
    j = batch.parse(json.dumps([{"prompt": "a", "preset": "turbo"}]), ".json")[0]
    assert j["preset"] == "turbo"


def test_json_bare_list_of_strings():
    assert [j["prompt"] for j in batch.parse('["a","b"]', ".json")] == ["a", "b"]


def test_json_bare_list_of_objects():
    assert batch.parse('[{"prompt":"a"}]', ".json")[0]["prompt"] == "a"


def test_prompts_key_is_accepted_too():
    assert batch.parse('{"prompts": ["a"]}', ".json")[0]["prompt"] == "a"


def test_malformed_json_names_the_problem():
    with pytest.raises(ValueError) as e:
        batch.parse("{not json", ".json")
    assert "JSON" in str(e.value)


def test_a_bare_number_is_refused():
    with pytest.raises(ValueError):
        batch.parse("42", ".json")


def test_blank_prompts_are_dropped():
    assert batch.parse('["", "  ", "real"]', ".json") == [
        j for j in batch.parse('["real"]', ".json")]


def test_out_of_range_numbers_are_clamped_not_rejected():
    j = batch.parse(json.dumps([{"prompt": "a", "seconds": 900, "takes": 99}]),
                    ".json")[0]
    assert j["seconds"] == 15 and j["takes"] == 10


def test_unknown_mode_is_left_for_the_caller_to_default():
    j = batch.parse(json.dumps([{"prompt": "a", "mode": "x2v"}]), ".json")[0]
    assert j["mode"] is None


def test_an_oversized_batch_is_refused():
    many = json.dumps([f"prompt {i}" for i in range(batch.MAX_JOBS_PER_BATCH + 1)])
    with pytest.raises(ValueError) as e:
        batch.parse(many, ".json")
    assert "limit" in str(e.value)


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


async def test_unpack_zip_stores_images_and_maps_them(s3):
    jobs, images, _ = await batch.unpack_zip(
        _zip({"prompts.txt": b"a clip\n", "ref.png": b"\x89PNG-not-really"}),
        "u1", s3)
    assert [j["prompt"] for j in jobs] == ["a clip"]
    assert images["ref.png"].startswith("uploads/u1/")
    assert await s3.get(images["ref.png"]) == b"\x89PNG-not-really"


async def test_zip_entries_cannot_escape_the_prefix(s3):
    _, images, _ = await batch.unpack_zip(
        _zip({"../../etc/passwd.png": b"x", "nested/dir/ok.png": b"y"}), "u1", s3)
    assert all(k.startswith("uploads/u1/") for k in images.values())
    assert set(images) == {"passwd.png", "ok.png"}


async def test_windows_style_zip_paths_are_flattened_too(s3):
    _, images, _ = await batch.unpack_zip(
        _zip({"..\\..\\windows\\evil.png": b"x"}), "u1", s3)
    assert set(images) == {"evil.png"}


async def test_json_ref_names_survive_into_the_jobs(s3):
    jobs, images, _ = await batch.unpack_zip(
        _zip({"b.json": json.dumps([{"prompt": "a", "image": "ref.png"}]).encode(),
              "ref.png": b"img"}), "u1", s3)
    assert jobs[0]["ref_names"] == ["ref.png"]
    assert "ref.png" in images


async def test_a_corrupt_archive_says_so(s3):
    with pytest.raises(ValueError) as e:
        await batch.unpack_zip(b"this is not a zip", "u1", s3)
    assert "zip" in str(e.value).lower()


async def test_a_zip_bomb_is_refused_before_it_is_read(s3):
    huge = _zip({f"f{i}.png": b"x" * 1024 for i in range(3)})
    original = batch.MAX_ZIP_TOTAL_BYTES
    batch.MAX_ZIP_TOTAL_BYTES = 2048
    try:
        with pytest.raises(ValueError) as e:
            await batch.unpack_zip(huge, "u1", s3)
        assert "512 MB" in str(e.value) or "MB" in str(e.value)
    finally:
        batch.MAX_ZIP_TOTAL_BYTES = original


# ---- the track a clip has to follow ----

def test_a_job_can_name_the_track_it_follows():
    j = batch.parse(json.dumps([{"prompt": "a", "audio": "seg01.wav"}]), ".json")[0]
    assert j["audio_name"] == "seg01.wav"


def test_a_job_that_names_no_track_says_so_rather_than_guessing():
    assert batch.parse("just a prompt\n", ".txt")[0]["audio_name"] is None


def test_a_named_track_cannot_point_outside_its_own_archive():
    j = batch.parse(json.dumps([{"prompt": "a", "audio": "../../etc/passwd.wav"}]),
                    ".json")[0]
    assert j["audio_name"] == "passwd.wav"


def test_defaults_can_carry_a_track_like_any_other_field():
    text = json.dumps({"defaults": {"audio": "same.wav"},
                       "jobs": [{"prompt": "a"}, {"prompt": "b", "audio": "own.wav"}]})
    a, b = batch.parse(text, ".json")
    assert (a["audio_name"], b["audio_name"]) == ("same.wav", "own.wav")


@needs_ffmpeg
async def test_a_zip_stores_its_audio_as_one_format(s3):
    """Whatever the archive was assembled from, the pod meets a stereo 48k wav."""
    jobs, _, audios = await batch.unpack_zip(
        _zip({"b.json": json.dumps([{"prompt": "a", "audio": "line.mp3"}]).encode(),
              "line.mp3": _tone(fmt="mp3", codec="libmp3lame")}), "u1", s3)
    assert jobs[0]["audio_name"] == "line.mp3"
    key = audios["line.mp3"]
    assert key.startswith("uploads/u1/") and key.endswith(".wav")
    with wave.open(io.BytesIO(await s3.get(key))) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (2, 48000, 2)


@needs_ffmpeg
async def test_a_track_longer_than_the_guide_window_is_cut_to_it(s3):
    _, _, audios = await batch.unpack_zip(
        _zip({"long.wav": _tone(seconds=MAX_GUIDE_AUDIO_SECONDS + 9)}), "u1", s3)
    with wave.open(io.BytesIO(await s3.get(audios["long.wav"]))) as w:
        assert w.getnframes() / 48000 == pytest.approx(MAX_GUIDE_AUDIO_SECONDS,
                                                       abs=0.1)


async def test_audio_that_cannot_be_read_is_dropped_not_stored(s3):
    """It leaves the job naming a track that is not there, which the route refuses."""
    jobs, _, audios = await batch.unpack_zip(
        _zip({"b.json": json.dumps([{"prompt": "a", "audio": "line.wav"}]).encode(),
              "line.wav": b"not audio at all"}), "u1", s3)
    assert jobs[0]["audio_name"] == "line.wav" and audios == {}


@needs_ffmpeg
async def test_audio_entry_names_are_flattened_like_every_other_entry(s3):
    _, _, audios = await batch.unpack_zip(
        _zip({"sounds/nested/seg01.wav": _tone()}), "u1", s3)
    assert set(audios) == {"seg01.wav"}
