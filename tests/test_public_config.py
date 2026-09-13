"""What the browser learns about presets: only native sizes are offered, and every
preset comes with the waiting time the picker shows next to it."""
from app.config import Config
from app.estimate import minutes_per_clip


def test_only_the_native_canvas_is_offered():
    cfg = Config()
    offered = {k for k, p in cfg.generation.presets.items() if not p.hidden}
    assert offered == {"final", "turbo", "hd720"}
    for key in offered:
        p = cfg.generation.presets[key]
        assert (p.width, p.height) == (1344, 768), key
    # Hidden ones still resolve, so old rows and batch files keep working.
    assert cfg.generation.preset("draft").hidden
    assert cfg.generation.preset("hd1080").hidden


def test_public_config_carries_a_minutes_table():
    cfg = Config()
    public = cfg.public()
    table = public["estimate"]
    assert table["confidence"] == "estimated"
    assert set(table["minutes_per_10s"]) == set(cfg.generation.presets)
    gpu = table["gpu"]
    final = cfg.generation.presets["final"]
    assert table["minutes_per_10s"]["final"] == round(minutes_per_clip(gpu, final, 10, cfg), 2)
    # Turbo is 4 steps against Final's 30, so it is the cheap one.
    assert table["minutes_per_10s"]["turbo"] < table["minutes_per_10s"]["final"]
    # An enlargement runs no diffusion steps, so it costs nothing on this table.
    assert table["minutes_per_10s"]["up2x"] == 0


def test_a_measurement_flips_the_confidence():
    cfg = Config()
    cfg.measured_minutes_per_clip = 12.0
    table = cfg.estimate_table()
    assert table["confidence"] == "measured"
    assert table["minutes_per_10s"]["final"] == 12.0
