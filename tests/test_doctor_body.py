"""`app.doctor` checks the pod body against RunPod's schema before money is spent.

REST v2 nests the GPU settings and builds the request out of `allOf` and `$ref`,
so a check that only reads top-level `properties` finds nothing to compare and
passes anything. SPEC is a slice of the real v2 schema (api.runpod.io/v2/openapi.json,
2026-11-14), shaped the same way.
"""
from __future__ import annotations

from app.backends.runpod_pod import RunpodBackend
from app.config import Config
from app.doctor import _check_fields

_REF = "#/components/schemas/"
SPEC = {"components": {"schemas": {
    "Cloud": {"type": "string", "enum": ["SECURE", "COMMUNITY"]},
    "ContainerConfig": {"type": "object", "properties": {
        "image": {"type": "string"},
        "disk": {"type": "integer", "minimum": 1},
        "ports": {"type": "array", "items": {"type": "string"}},
        "entrypoint": {"type": "array", "items": {"type": "string"}},
        "registry": {"type": ["string", "null"]},
    }},
    "CreateGpuConfig": {"type": "object", "required": ["id"], "unevaluatedProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "count": {"type": "integer", "default": 1},
                            "allowedCudaVersions": {"type": "array", "items": {"type": "string"}},
                            "minRamPerGpu": {"type": "integer"},
                        }},
    "Mounts": {"type": "object", "properties": {"network": {"type": "array"}}},
    "CreatePodRequest": {"unevaluatedProperties": False, "allOf": [
        {"$ref": _REF + "ContainerConfig"},
        {"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string"},
            "cloud": {"allOf": [{"$ref": _REF + "Cloud"}], "default": "SECURE"},
            "gpu": {"$ref": _REF + "CreateGpuConfig"},
            "mounts": {"$ref": _REF + "Mounts"},
            "dataCenterIds": {"type": "array", "items": {"type": "string"}},
        }},
    ]},
}}}
CREATE = SPEC["components"]["schemas"]["CreatePodRequest"]


async def test_the_pod_body_passes_the_v2_schema():
    cfg = Config()
    cfg.runpod.network_volume_id = "vol-1"
    cfg.runpod.data_center_ids = ["EU-RO-1"]
    backend = RunpodBackend(cfg)
    try:
        for gpu in ("NVIDIA GeForce RTX 5090", "NVIDIA H100 PCIe"):
            assert _check_fields(SPEC, CREATE, backend.build_create_body(gpu, "COMMUNITY")) == []
    finally:
        await backend.aclose()


def test_every_field_v2_would_refuse_is_named():
    body = {"name": "h3studio-1", "imageName": "x", "cloud": "ALL",
            "gpu": {"id": "NVIDIA GeForce RTX 5090", "count": "1", "minRAMPerGPU": 80}}
    bad = "\n".join(_check_fields(SPEC, CREATE, body))
    assert "'imageName'" in bad                  # v1's name for `image`
    assert "cloud: 'ALL'" in bad                 # an enum behind allOf + $ref
    assert "gpu.count: sending str" in bad       # a type inside the nested object
    assert "'gpu.minRAMPerGPU'" in bad           # v1's name, one level down
