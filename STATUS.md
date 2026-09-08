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

## NOT yet proven

- **ComfyUI actually starting on the pod.** The one run that got that far died
  because the weight paths were wrong (now fixed). Nothing has yet confirmed the
  bootstrap script finds ComfyUI, downloads 55.9GB and serves on :8188.
- **The MiniMax H3 nodes existing in the image.** `app.smoketest` checks this.
- **Workflow templates.** `app/workflows/h3_t2v.api.json` and `h3_i2v.api.json`
  do not exist. Without them every job fails with an explanatory error.
  They must be exported once from a running pod's ComfyUI.
- **Any video ever generated on a real GPU.**

## The immediate next step

```powershell
.venv\Scripts\python -m app.smoketest --keep --budget 2
```

Expect 7–20 minutes for the 55.9GB download. `--keep` leaves the pod up so the
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
   15 minutes. → `check_weights`

## Decisions that look odd but are deliberate

- **INT8 weights, not NVFP4.** The FP4 encoder is half the size but Blackwell-only.
  Pairing it with A6000/L40S fallbacks made the config internally inconsistent —
  the cheap card would be chosen automatically and then fail. INT8 runs everywhere.
- **`volumeInGb: 0`.** RunPod otherwise attaches 20GB at `/workspace`, which would
  both mask a ComfyUI installed there and be far too small for the weights.
- **No network volume.** ~$10/month billed even while off, versus ~5 cents to
  re-download per session. Only worth it if generating most days.
- **The bootstrap searches for ComfyUI** rather than assuming a path. A wrong guess
  only surfaces minutes into a pod that is already billing.
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

## Housekeeping

- The RunPod API key ending `jw88` was accidentally printed in full during a session
  on 2026-09-08 and **should be revoked and replaced** if that has not been done.
- `config.yaml` is gitignored and holds the key. Never `cat` it — `app.doctor` and
  `/api/status` report only a boolean and the last four characters.
