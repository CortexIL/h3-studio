"""How the pod is told to fetch 84GB from HuggingFace.

Boot is most of what a second pod costs, and the pod was downloading
anonymously - HF rate-limits that and says so in the log. These pin the two
things that address it, and pin that a token set here does not leak when it is
not.
"""
from __future__ import annotations

import shlex

from app.backends.runpod_pod import _bootstrap_cmd
from app.config import Config


def _script(token: str = "") -> str:
    cfg = Config()
    cfg.runpod.hf_token = token
    return " ".join(_bootstrap_cmd(cfg))


def test_the_fast_downloader_is_asked_for_whether_or_not_a_token_is_set():
    """hf_transfer needs no account, so it is not conditional on the token."""
    for token in ("", "hf_secret123"):
        script = _script(token)
        assert "hf_transfer" in script
        assert "HF_HUB_ENABLE_HF_TRANSFER=1" in script


def _exported_token(script: str) -> str:
    """The value the pod's shell would actually end up with."""
    line = next(ln for ln in script.split("\n") if "export HF_TOKEN=" in ln)
    return shlex.split(line.strip())[1].split("=", 1)[1]


def test_a_configured_token_reaches_the_pod():
    assert _exported_token(_script("hf_secret123")) == "hf_secret123"


def test_no_token_configured_leaves_no_export_and_says_so():
    script = _script("")
    assert "HF_TOKEN" not in script
    assert "anonymously" in script, "the log should explain why the boot is slow"


def test_a_token_with_shell_characters_cannot_break_out_of_the_script():
    """It is pasted from a web page into a config file, so treat it as hostile.

    The characters may well appear in the script - quoted. What must not happen
    is the shell reading them as anything but one value.
    """
    hostile = "hf_x'; rm -rf / #"
    assert _exported_token(_script(hostile)) == hostile
