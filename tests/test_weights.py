"""What the pod downloads before it can render anything.

An empty manifest is the expensive failure mode: the pod boots, ComfyUI starts
with no models, and the mistake is only visible after a GPU has been rented. So
the manifest is checked against the templates themselves.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backends.runpod_pod import _bootstrap_cmd, min_ram_for
from app.config import Config

TEMPLATES = sorted((Path(__file__).resolve().parent.parent / "app" / "workflows").glob("*.json"))


def _models(graph: dict) -> set[str]:
    """Every model file a template loads, by basename - how ComfyUI matches them."""
    found = set()
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        for field, value in (node.get("inputs") or {}).items():
            if field.endswith("_name") and isinstance(value, str) and value.endswith(".safetensors"):
                found.add(value.rsplit("/", 1)[-1])
    return found


def test_the_manifest_is_not_empty():
    assert Config().weights.files, "a pod with no weights renders nothing"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_every_model_a_template_loads_is_downloaded(template):
    manifest = {f.src.rsplit("/", 1)[-1] for f in Config().weights.files}
    missing = _models(json.loads(template.read_text(encoding="utf-8"))) - manifest
    assert not missing, f"{template.name} loads models the pod never downloads: {sorted(missing)}"


def test_every_preset_lora_is_downloaded():
    cfg = Config()
    manifest = {f.src.rsplit("/", 1)[-1] for f in cfg.weights.files}
    for name, preset in cfg.generation.presets.items():
        if preset.lora:
            assert preset.lora.rsplit("/", 1)[-1] in manifest, f"preset {name}"


def test_the_pod_disk_holds_the_weights_and_the_image():
    cfg = Config()
    # The ComfyUI image is tens of GB on its own, so the disk needs real headroom
    # over the weights, not a few spare GB.
    assert cfg.runpod.container_disk_gb >= cfg.weights.total_gb_hint() + 40


def test_the_pod_asks_for_ram_that_holds_the_two_largest_models():
    cfg = Config()
    two_largest = sum(sorted(f.gb or 0 for f in cfg.weights.files)[-2:])
    assert min_ram_for(cfg) >= two_largest


def test_the_bootstrap_downloads_every_file():
    cfg = Config()
    script = "\n".join(_bootstrap_cmd(cfg))
    for f in cfg.weights.files:
        assert f.src in script
        assert f"download failed: {f.src.rsplit('/', 1)[-1]}" in script
