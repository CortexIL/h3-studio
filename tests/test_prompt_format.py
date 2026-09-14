"""The prompt the model reads follows MiniMax's own format, mode by mode."""
from app.config import Config
from app.workflows import assemble_prompt, build_workflow


def test_an_image_clip_opens_with_the_first_frame_instruction():
    text = assemble_prompt({"prompt": "she turns", "mode": "i2v", "ref_images": ["uploads/u/a.png"]})
    lines = text.split("\n\n")
    assert lines[0] == ("For the target video, at 0.00 seconds into the target video, "
                        "<Picture 1> (from [Shot 1]) is fully referenced.")
    assert lines[1] == "integrated_multimodal_description: [Shot 1] she turns"


def test_words_only_has_no_instruction_line():
    assert assemble_prompt({"prompt": "a storm", "mode": "t2v"}) == "integrated_multimodal_description: [Shot 1] a storm"


def test_start_to_end_names_both_pictures_and_the_length():
    text = assemble_prompt({"prompt": "[Shot 1] she stands. [Shot 2] At 00:04.000, the camera cuts to the door.",
                            "mode": "flf2v", "seconds": 8, "ref_images": ["a.png", "b.png"]})
    assert text.startswith("How the reference pictures align with the target video — Picture 1 (from Shot 1) "
                           "aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 2) "
                           "aligns with the 8.00-second mark of the target video.")
    # A description that already carries shot labels is not wrapped again.
    assert "[Shot 1] [Shot 1]" not in text


def test_a_continuation_with_an_arrival_image_uses_the_last_frame_line():
    text = assemble_prompt({"prompt": "he walks on", "mode": "extend", "seconds": 6,
                            "ref_images": ["tail.mp4", "arrive.png"]})
    assert text.startswith("How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) "
                           "aligns with the 6.00-second mark of the target video.")
    # ... and without one there is nothing to align.
    plain = assemble_prompt({"prompt": "he walks on", "mode": "extend", "ref_images": ["tail.mp4"]})
    assert plain == "integrated_multimodal_description: [Shot 1] he walks on"


def test_references_mode_keeps_the_tagged_text_as_written():
    text = assemble_prompt({"prompt": "<Picture 1> is the woman", "mode": "r2v", "ref_images": ["a.png"], "sound": "rain"})
    assert text == "<Picture 1> is the woman\n\noverall_soundscape: rain"


def test_sound_off_drops_the_sound_fields():
    text = assemble_prompt({"prompt": "p", "mode": "t2v", "keep_audio": False, "sound": "wind", "music": "piano"})
    assert text == "integrated_multimodal_description: [Shot 1] p"


def test_effects_become_embedding_tokens_at_the_front():
    text = assemble_prompt({"prompt": "a duel", "mode": "t2v", "effects": ["bullet_time", "nope", "storm_magic"]})
    assert text == ("integrated_multimodal_description: [Shot 1] "
                    "embedding:minimaxh3_bullet_time embedding:minimaxh3_storm_magic a duel")


def test_the_graph_carries_the_structured_prompt_with_the_picture_line():
    graph = build_workflow({"prompt": "a lantern", "mode": "i2v", "preset": "turbo", "seconds": 5,
                            "ref_images": ["uploads/u/a.png"]}, Config())
    prompts = [n["inputs"]["prompt"] for n in graph.values()
               if n.get("class_type", "").startswith("MiniMaxH3") and "prompt" in n["inputs"]]
    assert prompts and prompts[0].startswith("For the target video, at 0.00 seconds")
