# Where this project stands

Written 2026-09-08. Read this first if you are picking the project up cold —
it records what has been *proven* versus what is still assumed, and why several
non-obvious decisions are the way they are.

---

## Proven against the real world

| Thing | How it was proven |
|---|---|
| RunPod API key + auth scheme | `app.doctor` — real 200 from `GET /pods` |
| GPU type identifiers | validated against RunPod's live OpenAPI enum |
| Container image tag | checked against Docker Hub |
| `POST /pods` request body | accepted by RunPod; a pod really was created |
| Pod creation + GPU fallback | A6000 had no capacity, code fell through to L40S by itself |
| Real hourly price | RunPod reported $0.79/hr for L40S; the app uses the reported figure, not its own table |
| Weight file paths | all 5 verified against the HuggingFace manifest |
| Queue, editor, inbox, ZIP intake, MCP server, quit button | exercised end to end in demo mode |

## Learned from the pod that did boot (read its logs, they were decisive)

- **The image ignores `dockerStartCmd`.** It declares `ENTRYPOINT ["/start.sh"]` with
  no CMD, so anything passed as dockerStartCmd arrives as *arguments* to /start.sh and
  is silently dropped. The bootstrap now goes in `dockerEntrypoint` and ends with
  `exec /start.sh`, so the image still sets up its venv, FileBrowser and Jupyter.
- **ComfyUI lives at `/workspace/runpod-slim/ComfyUI`,** not any of the five paths
  that were being guessed. Weights now go to `/workspace/h3models` and are wired in
  through `--extra-model-paths-config`, which is ComfyUI's supported mechanism and
  needs no write access to a directory the image manages.
- **The host driver, not the image, was killing ComfyUI.** Two boots died on the same
  machine: cu128 with a vague `CUDA unknown error`, cu130 with the explicit
  `The NVIDIA driver on your system is too old (found version 12040)`. That host's
  driver supported only CUDA 12.4. `PodCreateInput.allowedCudaVersions` exists exactly
  for this and is documented as *"if not set, any CUDA version is acceptable"* - so
  leaving it unset was our omission. It is now derived from the image tag, and the
  image is back on cuda12.8 because that accepts 12.8/12.9/13.0 hosts rather than
  13.0 only.
- **The pod reports its own progress now.** A tiny server holds :8188 and answers 503
  with the current bootstrap step until ComfyUI takes over. Before this, a script that
  died in its first seconds looked exactly like a slow 56GB download.

## Proven on the third pod (which still failed, but only at the last step)

- `dockerEntrypoint` runs - the bootstrap script executed for the first time.
- The status server works: live `downloading 3/5: ...` instead of a blind 502.
- All 55.9GB downloaded in **10 minutes**, matching the estimate almost exactly.
- ComfyUI read the config: `Adding extra search path diffusion_models / loras /
  text_encoders / vae`.

Everything up to the CUDA line worked. Only the host driver failed.

## NOT yet proven

- **ComfyUI actually starting.** With `allowedCudaVersions` set this should now land
  on a machine that can run it, but no pod has reached a working ComfyUI yet.
- **The MiniMax H3 nodes existing in the image.** `app.smoketest` checks this.
- **Workflow templates.** `app/workflows/h3_t2v.api.json` and `h3_i2v.api.json`
  do not exist. Without them every job fails with an explanatory error.
  They must be exported once from a running pod's ComfyUI.
- **Any video ever generated on a real GPU.**

## The immediate next step

```powershell
.venv\Scripts\python -m app.smoketest --keep --budget 2
```

Expect ~10-20 minutes for the 55.9GB download. Unlike before, it prints a live line
per step (`downloading 3/5: ...`), so a stall is now distinguishable from progress. `--keep` leaves the pod up so the
workflow templates can be exported in the same session rather than paying twice.

Then, in the pod's ComfyUI: Templates → MiniMax H3 (T2V) → Workflow → Export (API),
save as `app/workflows/h3_t2v.api.json`. Repeat for I2V.

**Always finish with:**

```powershell
.venv\Scripts\python -m app.killpods
```

---

## Three paid failures, three free checks

Every check in `app.doctor` that looks paranoid was bought with a real failure.
None of them should be removed.

1. **`runpod/comfyui:0.30.0` did not exist.** A non-existent tag still lets pod
   creation succeed; the container then never starts and it looks like a slow
   boot. → `check_image`
2. **`dockerStartCmd` was a string; the schema wants an array.** RunPod rejected it
   at validation for $0, but one field at a time. → `check_pod_body` validates the
   whole payload at once.
3. **All four weight paths were wrong,** invented from a blog post rather than the
   repo manifest. The first download failed, `set -eu` killed the bootstrap, and
   ComfyUI never started — indistinguishable from a slow download. Cost ~$0.20 and
   15 minutes. → `check_weights`, which also caches the real sizes into config.yaml so
   no "this downloads NN GB" message can drift out of date again.

Two further failures cost ~$0.40 between them, and both were only diagnosable from
the pod's **container log** in the RunPod console - not from any API. The first showed
the image ignoring dockerStartCmd; the second showed the host driver being too old.
When a pod boots but ComfyUI never answers, that log is the first place to look, and
it has been decisive every single time.

Running total across every attempt: about **$1.10**.

4. **`minRAMPerGPU` was never set,** so RunPod applied its 8GB default and gave a
   5090 pod 46GB of RAM for 48GB of weights. ComfyUI stages each model through system
   RAM on its way to the GPU, so the host swapped to disk: the GPU idled at 3.7GB of
   34, nothing errored, and a four-minute clip had not moved after twenty-nine. Cost
   ~$0.68 for zero output. → `_check_ram`, and the requirement is now derived from the
   two largest checkpoints plus headroom.

The pattern is consistent enough to be a rule: **every field left unset in the pod
request became a silent failure at RunPod's default.** Image tag, CUDA version, RAM.
When adding anything to that payload, ask what the default is before omitting it.

## Decisions that look odd but are deliberate

- **INT8 weights, not NVFP4.** The FP4 encoder is half the size but Blackwell-only.
  Pairing it with A6000/L40S fallbacks made the config internally inconsistent —
  the cheap card would be chosen automatically and then fail. INT8 runs everywhere.
- **`volumeInGb: 0`.** RunPod otherwise attaches 20GB at `/workspace`, which would
  both mask a ComfyUI installed there and be far too small for the weights.
- **No network volume.** ~$10/month billed even while off, versus ~5 cents to
  re-download per session. Only worth it if generating most days.
- **The bootstrap never touches the image's ComfyUI directory.** Weights land in
  `/workspace/h3models` and are declared through `--extra-model-paths-config`, so the
  image's own first-time copy cannot clobber them and nothing depends on guessing
  where ComfyUI was installed.
- **The bootstrap has no `set -e`.** A failing step parks the pod with a readable
  status instead of exiting, because RunPod reports a dead container exactly like a
  working one - the failure has to stay visible to be diagnosed.
- **4xx fails immediately, 5xx tries the next GPU.** Retrying a malformed request on
  three cards wastes time and buries the real reason.
- **The power button is a policy, not a switch.** Five people cannot each hold a
  switch over one shared pod; and `auto` is also what stops a pod running overnight.
- **Workflow values are patched by `class_type`, never node id,** so re-exporting a
  template from a newer ComfyUI does not break anything.
- **The web page holds no logic and no secrets.** That is what makes moving to a
  shared team instance a deployment rather than a rewrite.

## The biggest untapped cost lever

`loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` — 4 steps
instead of 30, roughly **seven times less compute per clip**, at exactly the 768p
the open weights top out at. It is already in the download list, but downloading it
does nothing on its own: the exported workflow needs a LoRA loader node wired in.
Do this when exporting the templates.

## Safety nets, in order of how likely they are to matter

1. **`python -m app.killpods`** — terminates everything. The panic button.
2. Idle shutdown (`pod.idle_shutdown_minutes`, 10).
3. Budget ceiling (`budget.session_limit_usd`, $8) — kills the pod and flips policy to off.
4. Session ceiling (`pod.max_session_hours`, 6).
5. Pod shutdown sits in the lifespan `finally`, so Ctrl-C and the Quit button both reach it.
6. [console.runpod.io/pods](https://console.runpod.io/pods) is the only real source of truth.

## The first real batch (2026-09-09) — 16 clips, 0 failures

Measured, not estimated. RTX 5090 @ $0.69/hr, 1344x768, 10s, i2v.

| preset | steps | render/clip | $/clip |
|---|---|---|---|
| `final` | 30 | 32.4 min | $0.37 |
| `turbo` | 4 | ~5 min | $0.068 |

- **Sampling ran at 63 s/step on the 5090 and that was the entire cost of the batch.**
  The log said why: `Model MiniMaxH3 prepared for dynamic VRAM loading. 19995MB Staged.`
  20GB of weights plus the activations of a 240-frame 1344x768 latent do not fit in
  32GB, so ComfyUI streams the model across the PCIe bus on every step. VRAM
  oscillating between 5 and 23GB is that, and it is *healthy* — a flat low number is
  the stall. At 30 steps the 15-clip batch was 8.3 hours and $5.60, which the 6-hour
  session ceiling would have cut off around clip 11.
- **`ComfyUI /internal/logs/raw` is the diagnostic that settled it.** Plain HTTP GET,
  returns the container console including the sampler's progress bar. Nothing else
  exposes step-level progress; `/queue` and `/history` only say running or not.
  Reach for it first when a pod is up but nothing is landing.
- **The 4-step turbo LoRA is the largest cost lever in the project** — 5.4x cheaper,
  and the reason the batch finished at ~$1.43 total. `Preset.lora` splices a
  `LoraLoaderModelOnly` between `UNETLoader` and every consumer of its `model`
  output, found structurally rather than by node id so it survives a re-export.

### Two things that bit, both worth remembering

- **Config is read once, at startup.** Adding a preset to `config.yaml` while the app
  is running does nothing, and `GenerationCfg.preset()` falls back to `Preset()` for
  an unknown name — 768x432, 20 steps, no LoRA — *silently*. A job written straight
  into the DB with a preset the live process has never heard of renders the wrong
  thing and reports success. Restart after touching presets. (Note that a restart
  terminates the pod by design; `adopt_existing()` is still untested in anger.)
- **Turbo's audio is noise.** Every turbo clip measures mean -13.9 dB +/- 0.1 with
  peaks near -5, against -34.8 dB for the 30-step clip: ~21 dB hot, and a *constant*
  RMS across 15 different clips, which is the signature of broadband noise rather
  than content. Not mixing, not stacking — each file carries exactly one AAC stream
  and the graph has exactly one `VAEDecodeAudio` -> one `CreateVideo`. The LoRA is
  `minimax_h3_fl2v_turbo_4step...` while the model is `minimax_h3_fl2va...`: the
  distillation covers the video branch, and the audio latent gets 4 steps of a
  schedule built for 30. If audio is ever needed, raise the step count or drop the
  LoRA. `output.keep_audio: false` now remuxes the stream away on save (`-c copy`,
  so the picture is untouched); it degrades to keeping the file as-is when ffmpeg
  is not installed, because a clip with unwanted audio beats no clip.

## Housekeeping

- A RunPod API key ending `jw88` was accidentally printed in full on 2026-09-08 and
  has since been replaced (the live key ends `oahz`). Revoke `jw88` on RunPod if that
  was not already done.
- `config.yaml` is gitignored and holds the key. Never `cat` it — `app.doctor` and
  `/api/status` report only a boolean and the last four characters, which is all
  anything needs.
