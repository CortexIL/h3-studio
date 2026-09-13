"""The reference timings must stay in the range the rented GPUs actually showed,
or the picker's "about N min" line lies to the person choosing a quality."""
from app.config import Config
from app.estimate import minutes_per_clip


def test_turbo_and_final_match_the_measured_sessions():
    cfg = Config()
    turbo = cfg.generation.presets["turbo"]
    final = cfg.generation.presets["final"]
    # Measured on 2026-09-12 (L40): a 5 s Turbo clip took about 4.7 minutes.
    assert 4.0 <= minutes_per_clip("NVIDIA L40", turbo, 5, cfg) <= 5.5
    # ... and a 9 s Final about an hour.
    assert 50 <= minutes_per_clip("NVIDIA L40", final, 9, cfg) <= 70
    # A card the table does not know falls back to the same order of magnitude.
    assert 50 <= minutes_per_clip("Some New Card", final, 10, cfg) <= 80
