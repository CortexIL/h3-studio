"""SageAttention: a faster attention kernel, patched in only where the pod has it."""
from __future__ import annotations

from app.backends.runpod_pod import RunpodBackend, _bootstrap_cmd
from app.config import Config
from app.workflows import LORA_NODE_ID, SAGE_NODE, SAGE_NODE_ID, SHIFT_NODE_ID, build_workflow, validate_graph


def _job(**over):
    return {"prompt": "p", "mode": "t2v", "preset": "turbo", "seconds": 5, **over}


def test_the_patch_is_last_in_the_model_chain():
    g = build_workflow(_job(shift_video=16.0), Config(), features={"sage"})
    sage = g[SAGE_NODE_ID]
    assert sage["class_type"] == SAGE_NODE and sage["inputs"]["sage_attention"] == "auto"
    assert sage["inputs"]["model"] == [SHIFT_NODE_ID, 0]
    assert g[SHIFT_NODE_ID]["inputs"]["model"] == [LORA_NODE_ID, 0]
    readers = [n for n in g.values() if n.get("inputs", {}).get("model") == [SAGE_NODE_ID, 0]]
    assert len(readers) >= 2                    # the guider and the scheduler
    validate_graph(g)


def test_a_pod_without_the_node_gets_the_stock_graph():
    plain = build_workflow(_job(seed=1), Config())
    assert plain == build_workflow(_job(seed=1), Config(), features=set())
    assert SAGE_NODE_ID not in plain


def test_the_switch_lives_upstream_of_the_graph():
    """The Admin switch decides whether "sage" is in the features a job carries;
    the graph builder applies whatever it is handed and reads no flag itself."""
    cfg = Config()
    cfg.generation.sage_attention = False
    assert SAGE_NODE_ID in build_workflow(_job(), cfg, features={"sage"})
    assert SAGE_NODE_ID not in build_workflow(_job(), cfg, features=set())


def test_an_upscale_is_never_patched():
    g = build_workflow({"mode": "upscale", "ref_images": ["v/c.mp4"], "source_frames": 10}, Config(), features={"sage"})
    assert SAGE_NODE_ID not in g


def test_the_bootstrap_installs_it_best_effort():
    script = "\n".join(_bootstrap_cmd(Config()))
    assert "pip install -q --no-cache-dir sageattention" in script
    assert "ComfyUI-KJNodes" in script
    assert "rendering the stock way" in script


class _Comfy:
    def __init__(self, has_node: bool, raise_: bool = False):
        self.has_node, self.raise_ = has_node, raise_
        self.prompts = []

    async def object_info(self, node=None):
        if self.raise_:
            raise RuntimeError("proxy blip")
        return {SAGE_NODE: {"input": {}}} if self.has_node else {}

    async def model_options(self):
        return {}

    async def queue_prompt(self, graph):
        self.prompts.append(graph)
        return "p1"


async def _submit(comfy):
    b = RunpodBackend(Config())
    b._comfy = comfy                    # type: ignore[assignment]
    await b.submit(_job())
    return comfy.prompts[-1]


async def test_the_backend_asks_the_pod_once_and_patches_when_it_can():
    comfy = _Comfy(has_node=True)
    b = RunpodBackend(Config())
    b._comfy = comfy                    # type: ignore[assignment]
    await b.submit(_job()); await b.submit(_job())
    assert all(SAGE_NODE_ID in g for g in comfy.prompts)
    assert b._features == {"sage"}


async def test_a_pod_that_lacks_it_or_cannot_be_asked_renders_the_stock_way():
    assert SAGE_NODE_ID not in await _submit(_Comfy(has_node=False))
    assert SAGE_NODE_ID not in await _submit(_Comfy(has_node=True, raise_=True))
